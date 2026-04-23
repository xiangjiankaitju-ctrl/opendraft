#!/usr/bin/env python3
"""
ABOUTME: Citation research orchestrator with intelligent fallback chain
ABOUTME: Coordinates Crossref/OpenAlex/OpenAIRE/CORE/DOAJ/Semantic Scholar/LLM fallback for high coverage
"""

import logging
import json
import os
import sys
import re
import time
from typing import Optional, Dict, Any, Tuple, List, Callable
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

from pydantic import ValidationError

# Safe print function that handles broken pipes (worker runs with stdio: 'ignore')
def safe_print(*args, **kwargs):
    """Print wrapper that catches BrokenPipeError and respects verbosity settings."""
    # In quiet mode, suppress detailed research output
    if not _verbose_research:
        return

    try:
        print(*args, **kwargs)
    except (BrokenPipeError, OSError):
        # Pipe is closed (worker running with stdio: 'ignore'), use logger instead
        message = ' '.join(str(arg) for arg in args)
        logger.debug(message)
        # Prevent further broken pipe errors by redirecting stdout
        try:
            sys.stdout = open(os.devnull, 'w')
        except:
            pass

from .crossref import CrossrefClient
from .openalex import OpenAlexClient
from .semantic_scholar import SemanticScholarClient
from .openaire import OpenAIREClient
from .core_client import COREClient
from .doaj import DOAJClient
from .query_router import QueryRouter, QueryClassification
from .base import validate_publication_year, validate_author_name, normalize_citation_metadata
from ..backpressure import BackpressureManager, APIType
from ..llm_provider import is_stop_finish_reason

from ..models import strip_markdown_json, LLMToolCitationResponse, RetrievalRelevanceAssessment

# =========================================================================
# Preprint Detection (Fix 3 from devil's advocate analysis)
# =========================================================================

# DOI prefixes that indicate preprints (not peer-reviewed)
PREPRINT_DOI_PREFIXES = [
    '10.2139/ssrn',       # SSRN
    '10.48550/arxiv',     # arXiv
    '10.1101/',           # bioRxiv/medRxiv
    '10.20944/preprints', # Preprints.org
    '10.31219/osf',       # OSF Preprints
    '10.21203/rs',        # Research Square
    '10.26434/chemrxiv',  # ChemRxiv
]

def is_preprint_doi(doi: str) -> bool:
    """Check if DOI indicates a preprint (not peer-reviewed)."""
    if not doi:
        return False
    doi_lower = doi.lower()
    return any(doi_lower.startswith(prefix) for prefix in PREPRINT_DOI_PREFIXES)

# Import existing Citation dataclass
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.citation_database import Citation

logger = logging.getLogger(__name__)
_backpressure = BackpressureManager()

# Global verbose flag for CLI mode control
_verbose_research = True


def set_research_verbosity(verbose: bool) -> None:
    """Control verbosity of research output for CLI mode."""
    global _verbose_research
    _verbose_research = verbose


class CitationResearcher:
    """
    Orchestrates citation research across multiple sources with intelligent fallback.

    Smart Routing (default):
    - Industry queries → Crossref/OpenAlex/OpenAIRE/CORE/DOAJ first
    - Academic queries → Crossref/OpenAlex first
    - Mixed queries → OpenAlex/Crossref first

    Classic Fallback chain (if smart routing disabled):
    1. Crossref API (best metadata, DOI-focused, academic papers)
    2. OpenAIRE / CORE / DOAJ (open-access and repository coverage)
    3. Semantic Scholar API (supplemental only, when primary academic APIs are insufficient)
    4. Tool-backed LLM fallback (last resort, traceable)

    Provides 95%+ success rate vs 40% LLM-only approach.
    Smart routing maximizes source diversity by routing to appropriate APIs first.
    Semantic Scholar is supplemental only and queried after the primary academic providers.
    """

    # Persistent cache file path
    CACHE_FILE = Path(".citation_cache_orchestrator.json")

    def __init__(
        self,
        llm_model: Optional[Any] = None,
        gemini_model: Optional[Any] = None,
        enable_crossref: bool = True,
        enable_openalex: bool = True,
        enable_semantic_scholar: bool = True,
        enable_openaire: bool = True,
        enable_core: bool = True,
        enable_doaj: bool = True,
        enable_llm_fallback: bool = True,
        enable_smart_routing: bool = True,
        verbose: bool = True,
        progress_callback: Optional[Callable[[str, str], None]] = None,
        enable_web_search: Optional[bool] = None,
        enable_chinese_databases: Optional[bool] = None,
        use_serper: Optional[bool] = None,
    ):
        """
        Initialize Citation Researcher.

        Args:
            llm_model: Generic model adapter for LLM fallback (optional)
            gemini_model: Backward-compatible alias for llm_model
            enable_crossref: Whether to use Crossref API
            enable_openalex: Whether to use OpenAlex API (250M+ works)
            enable_semantic_scholar: Whether to use Semantic Scholar API
            enable_openaire: Whether to use OpenAIRE API
            enable_core: Whether to use CORE API
            enable_doaj: Whether to use DOAJ API
            enable_llm_fallback: Whether to fall back to LLM if all else fails
            enable_smart_routing: Whether to use smart query routing (default: True)
            verbose: Whether to print progress
            progress_callback: Optional callback(message, event_type) for progress reporting
            enable_web_search: Deprecated legacy argument, ignored
            enable_chinese_databases: Deprecated legacy argument, ignored
            use_serper: Deprecated legacy argument, ignored
        """
        if enable_web_search is not None:
            logger.warning("CitationResearcher(enable_web_search=...) is deprecated and ignored")
        if enable_chinese_databases is not None:
            logger.warning("CitationResearcher(enable_chinese_databases=...) is deprecated and ignored")
        if use_serper is not None:
            logger.warning("CitationResearcher(use_serper=...) is deprecated and ignored")

        self.llm_model = llm_model or gemini_model
        self.gemini_model = self.llm_model  # backward compatibility alias
        self.progress_callback = progress_callback
        self.enable_crossref = enable_crossref
        self.enable_openalex = enable_openalex
        self.enable_semantic_scholar = enable_semantic_scholar
        self.enable_openaire = enable_openaire
        self.enable_core = enable_core
        self.enable_doaj = enable_doaj
        self.enable_llm_fallback = enable_llm_fallback and self.llm_model is not None
        self.enable_smart_routing = enable_smart_routing
        self.verbose = verbose
        # Supplemental Semantic Scholar budget control (reduce 429 bursts)
        self.semantic_scholar_budget_per_window = 4
        self.semantic_scholar_window_seconds = 60
        self._semantic_scholar_call_timestamps: List[float] = []

        # Initialize API clients
        if self.enable_crossref:
            self.crossref = CrossrefClient()
        if self.enable_openalex:
            self.openalex = OpenAlexClient()
        if self.enable_semantic_scholar:
            self.semantic_scholar = SemanticScholarClient()
        if self.enable_openaire:
            try:
                self.openaire = OpenAIREClient()
            except Exception as e:
                logger.warning(f"OpenAIRE client unavailable: {e}")
                self.enable_openaire = False
        if self.enable_core:
            try:
                self.core = COREClient()
            except Exception as e:
                logger.warning(f"CORE client unavailable: {e}")
                self.enable_core = False
        if self.enable_doaj:
            try:
                self.doaj = DOAJClient()
            except Exception as e:
                logger.warning(f"DOAJ client unavailable: {e}")
                self.enable_doaj = False
        # Initialize smart query router
        if self.enable_smart_routing:
            self.query_router = QueryRouter()

        # Load persistent cache (or initialize empty if file doesn't exist)
        self.cache: Dict[str, Optional[Tuple[Dict[str, Any], str]]] = self._load_cache()

        # Track source usage for round-robin variety (reset each session)
        self.source_usage_count: Dict[str, int] = {
            "Crossref": 0,
            "OpenAlex": 0,
            "Semantic Scholar": 0,
            "OpenAIRE": 0,
            "CORE": 0,
            "DOAJ": 0,
            "LLM Fallback": 0,
        }
        self.metrics: Dict[str, Any] = {
            "queries_total": 0,
            "queries_skipped": 0,
            "queries_executed": 0,
            "candidates_seen": 0,
            "candidates_accepted": 0,
            "candidates_rejected": 0,
            "provider_calls": {},
            "provider_success": {},
            "provider_rejects": {},
        }

    def _append_trace(self, event: Dict[str, Any]) -> None:
        """Append a structured research trace event for explainability/debugging."""
        try:
            event.setdefault("ts", int(time.time() * 1000))
            trace_log_path = Path(os.getenv("RESEARCH_TRACE_PATH", "research_trace.jsonl"))
            with open(trace_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"Trace write failed: {e}")

    def get_metrics_snapshot(self) -> Dict[str, Any]:
        snapshot = dict(self.metrics)
        total_candidates = max(1, snapshot.get("candidates_seen", 0))
        snapshot["accepted_rate"] = snapshot.get("candidates_accepted", 0) / total_candidates
        snapshot["relevance_pass_rate"] = snapshot.get("candidates_accepted", 0) / total_candidates
        snapshot["provider_health"] = {
            api.value: _backpressure.get_api_health(api)
            for api in APIType
            if api in {APIType.CROSSREF, APIType.SEMANTIC_SCHOLAR}
        }
        return snapshot

    def capability_matrix(self) -> Dict[str, Dict[str, Any]]:
        """Runtime capability snapshot for research preflight diagnostics."""
        return {
            "crossref": {"enabled": bool(self.enable_crossref)},
            "openalex": {"enabled": bool(self.enable_openalex)},
            "semantic_scholar": {
                "enabled": bool(self.enable_semantic_scholar),
                "cooled_down": _backpressure.is_api_cooled_down(APIType.SEMANTIC_SCHOLAR),
            },
            "openaire": {"enabled": bool(self.enable_openaire)},
            "core": {"enabled": bool(self.enable_core)},
            "doaj": {"enabled": bool(self.enable_doaj)},
        }

    def _is_chinese_query(self, topic: str) -> bool:
        """Heuristic detection for Chinese-language/CN database intent queries."""
        if not topic:
            return False
        if re.search(r'[\u4e00-\u9fff]', topic):
            return True

        lowered = topic.lower()
        markers = [
            'cnki', 'wanfang', 'cqvip', 'vip', 'sinomed', 'nssd',
            '中国知网', '知网', '万方', '维普', '中文数据库', '中文文献',
        ]
        return any(m in lowered or m in topic for m in markers)

    def _is_semantic_scholar_available(self) -> bool:
        """Check whether Semantic Scholar should be temporarily skipped due to cooldown."""
        if not self.enable_semantic_scholar:
            return False
        return not _backpressure.is_api_cooled_down(APIType.SEMANTIC_SCHOLAR)

    def _can_use_semantic_scholar_budget(self) -> bool:
        """Rate-budget guard for supplemental Semantic Scholar calls."""
        now = time.time()
        window_start = now - self.semantic_scholar_window_seconds
        self._semantic_scholar_call_timestamps = [
            ts for ts in self._semantic_scholar_call_timestamps if ts >= window_start
        ]
        if len(self._semantic_scholar_call_timestamps) >= self.semantic_scholar_budget_per_window:
            return False
        self._semantic_scholar_call_timestamps.append(now)
        return True

    def _sanitize_query(self, query: str, provider: Optional[str] = None) -> str:
        """Sanitize noisy planner queries before provider calls.

        Removes legacy web-search operators and punctuation that break academic APIs.
        """
        q = (query or "").strip()
        if not q:
            return ""

        # Normalize punctuation/quotes
        repl = {
            "：": ":", "，": " ", "；": " ", "（": "(", "）": ")",
            "“": '"', "”": '"', "‘": "'", "’": "'",
        }
        for src, dst in repl.items():
            q = q.replace(src, dst)

        # Remove web-era operators and site constraints
        q = re.sub(r"\bsite\s*:\s*\S+", " ", q, flags=re.IGNORECASE)
        q = re.sub(r"\btopic\s*:\s*", " ", q, flags=re.IGNORECASE)

        # For providers that choke on field operators, strip them
        if provider in {"core", "openaire", "doaj"}:
            q = re.sub(r"\b(author|title|abstract|keyword)\s*:\s*", " ", q, flags=re.IGNORECASE)
            q = q.replace(":", " ").replace("'", " ").replace('"', " ")

        if provider in {"core", "openaire"} and re.search(r'[\u4e00-\u9fff]', q):
            # These providers are unstable for raw Chinese queries; rely on Crossref/OpenAlex/DOAJ.
            return ""

        q = re.sub(r"\s+", " ", q).strip(" ' \"")
        return q

    def _rewrite_query_for_execution(self, query: str, classification: Optional[QueryClassification] = None) -> str:
        """Normalize low/medium-quality queries into provider-safer forms."""
        q = self._sanitize_query(query)
        if not q:
            return ""

        # Remove malformed mixed-script tokens like "new质productive".
        cleaned_tokens: List[str] = []
        for token in re.split(r"\s+", q):
            malformed = re.search(
                r'[A-Za-z]+[\u4e00-\u9fff]+[A-Za-z]+|[\u4e00-\u9fff]+[A-Za-z]{2,}[\u4e00-\u9fff]+',
                token,
            )
            if not malformed:
                cleaned_tokens.append(token)
        q = " ".join(cleaned_tokens).strip()

        # Collapse extremely long queries to the highest-signal front segment.
        parts = re.split(r'[,:;|]', q)
        if len(q) > 140 and parts:
            q = parts[0].strip()

        if classification and classification.query_quality == 'medium':
            q = re.sub(r"\b(report|analysis|framework|guidelines|policy|white paper|whitepaper)\b", " ", q, flags=re.IGNORECASE)
            q = re.sub(r"\s+", " ", q).strip()

        # Convert weak natural-language Chinese phrasing into keyword-style database query.
        if re.search(r'[\u4e00-\u9fff]', q):
            q = re.sub(r'[的对与和及在中研究影响作用提升优化改变增强促进方式系统生态]', ' ', q)
            q = re.sub(r'\s+', ' ', q).strip()
        else:
            q = re.sub(r'\b(on|of|for|the|and|into|with|using|study|research)\b', ' ', q, flags=re.IGNORECASE)
            q = re.sub(r'\s+', ' ', q).strip()

        return q

    def _regenerate_query_from_hint(self, query: str, classification: QueryClassification) -> str:
        """Deterministic one-shot regeneration for weak queries.

        Regeneration is intentionally rule-based to avoid introducing topic-specific
        LLM behavior into the retrieval stage.
        """
        q = self._sanitize_query(query)
        if not q:
            return ""

        is_chinese = self._is_chinese_query(q)
        if is_chinese:
            core = re.findall(r'[\u4e00-\u9fff]{2,}', q)
            core = [t for t in core if t not in {'影响研究', '作用研究', '方式研究'}]
            if not any(k in q for k in ['实证', '机制', '案例', '综述']):
                core.extend(['实证研究', '机制研究'])
            q = ' '.join(dict.fromkeys(core))
        else:
            tokens = re.findall(r'[A-Za-z][A-Za-z\-]{2,}', q)
            stop = {'impact', 'study', 'research', 'effect', 'effects', 'analysis'}
            tokens = [t for t in tokens if t.lower() not in stop]
            if not any(t.lower() in {'empirical', 'mechanism', 'review', 'case'} for t in tokens):
                tokens.extend(['empirical', 'study'])
            q = ' '.join(dict.fromkeys(tokens))

        return self._sanitize_query(q)

    def _has_method_signal(self, query: str) -> bool:
        """Check whether query carries research-method intent signals."""
        q = (query or "").lower()
        method_terms = {
            "empirical", "systematic review", "meta-analysis", "case study", "mechanism",
            "panel", "regression", "difference-in-differences", "literature review",
            "实证", "机制", "路径", "案例", "文献综述", "计量", "面板", "回归", "中介",
        }
        return any(term in q for term in method_terms)

    def _is_query_execution_worthy(self, query: str, classification: QueryClassification) -> Tuple[bool, str]:
        """Pre-execution quality gate to prevent low-signal query waste."""
        q = (query or "").strip()
        if not q:
            return (False, "empty query")

        if classification.query_quality == "low" or not classification.should_query:
            return (False, classification.rewrite_hint or "low-quality query")

        terms = self._extract_topic_terms(q)
        has_method = self._has_method_signal(q)
        has_zh = bool(re.search(r'[\u4e00-\u9fff]', q))

        # Medium-quality, low-confidence generic queries are the biggest source of noise.
        if classification.query_quality == "medium" and classification.confidence < 0.45:
            if len(terms) < 3 and not has_method:
                return (False, "low-confidence generic query without method/entity anchors")

        # Keep bilingual/CJK queries concise and anchored.
        if has_zh and len(terms) < 2:
            return (False, "underspecified CJK query")

        return (True, "")

    def _build_quality_aware_api_chain(
        self,
        classification: QueryClassification,
        topic_clean: str,
    ) -> List[str]:
        """Build provider chain by query quality and confidence budget."""
        base_chain = list(classification.api_chain or [])
        quality = classification.query_quality
        confidence = classification.confidence

        # Strong queries can use full chain; medium queries use a narrower academic core.
        if quality == "high" and confidence >= 0.55:
            chain = base_chain
        elif quality in {"high", "medium"}:
            chain = [api for api in base_chain if api in {"crossref", "openalex", "doaj"}]
        else:
            chain = [api for api in base_chain if api in {"crossref", "openalex"}]

        # Open repository providers are only appended for strong queries.
        if quality == "high" and confidence >= 0.60:
            for extra_api in ("openaire", "core"):
                if extra_api not in chain and extra_api in base_chain:
                    chain.append(extra_api)

        # Chinese/CJK queries remain on stable academic providers first.
        if self._is_chinese_query(topic_clean):
            preferred = ["crossref", "openalex", "doaj", "openaire", "core", "semantic_scholar"]
            chain = [a for a in preferred if a in chain] + [a for a in chain if a not in preferred]

        return chain

    def _extract_topic_terms(self, text: str) -> List[str]:
        """Extract lightweight topic terms for adaptive relevance checks."""
        t = (text or "").lower()
        en = re.findall(r"[a-z][a-z0-9\-]{2,}", t)
        zh = re.findall(r"[\u4e00-\u9fff]{2,}", text or "")
        stop_en = {"study", "research", "analysis", "review", "based", "using", "impact", "effect", "report", "policy", "framework"}
        terms = [w for w in en if w not in stop_en]
        terms.extend(zh)
        bilingual_map = {
            "artificial intelligence": ["ai", "人工智能"],
            "ai": ["artificial intelligence", "人工智能"],
            "productivity": ["生产率", "全要素生产率"],
            "新质生产力": ["new quality productive forces", "productive forces", "productivity"],
            "数字经济": ["digital economy"],
            "产业升级": ["industrial upgrading"],
            "智能制造": ["intelligent manufacturing", "smart manufacturing"],
            "算力": ["compute infrastructure", "computing power"],
            "数据要素": ["data factors", "data elements"],
        }
        expanded_terms: List[str] = []
        for term in list(terms):
            expanded_terms.append(term)
            for key, aliases in bilingual_map.items():
                if term == key or term in aliases:
                    expanded_terms.extend([key, *aliases])
        terms = expanded_terms
        # keep unique order
        seen = set()
        out = []
        for w in terms:
            if w not in seen:
                seen.add(w)
                out.append(w)
        return out[:20]

    def _score_topic_result_relevance(self, topic: str, metadata: Dict[str, Any]) -> float:
        """Compute a lightweight relevance score for a candidate citation."""
        text = " ".join([
            str(metadata.get("title", "") or ""),
            str(metadata.get("journal", "") or ""),
            str(metadata.get("publisher", "") or ""),
            str((metadata.get("abstract") or metadata.get("snippet") or ""))[:800],
        ]).lower()

        topic_terms = self._extract_topic_terms(topic)
        if not topic_terms:
            return 0.5

        overlap = sum(1 for term in topic_terms if term.lower() in text)
        overlap_ratio = overlap / max(1, min(len(topic_terms), 10))

        title_text = str(metadata.get("title", "") or "").lower()
        title_overlap = sum(1 for term in topic_terms[:8] if term.lower() in title_text)
        score = min(1.0, overlap_ratio * 0.65 + (title_overlap / max(1, min(len(topic_terms[:8]), 6))) * 0.35)

        negative_domains = {
            "diabetes", "glucose", "clinical", "patient", "hospital", "therapy",
            "metabolic", "mortality", "epigenetic", "pediatric", "movie", "film",
            "water diplomacy", "irrigation", "biomolecule", "ship", "forestry",
            "teaching reform", "course", "curriculum", "employment ability",
            "hiv", "aids", "oncology", "tumor", "cancer", "virology", "nursing",
        }
        trust_bonus = 0.0
        if metadata.get('doi'):
            trust_bonus += 0.08
        if metadata.get('journal'):
            trust_bonus += 0.05
        if metadata.get('source_type') in {'journal', 'conference', 'report'}:
            trust_bonus += 0.04

        negative_hits = sum(1 for w in negative_domains if w in text)
        score = score + trust_bonus - min(0.45, negative_hits * 0.12)
        return max(0.0, min(1.0, score))

    def _is_topic_result_relevant(self, topic: str, metadata: Dict[str, Any]) -> bool:
        """Topic-adaptive relevance gate with minimum score threshold."""
        score = self._score_topic_result_relevance(topic, metadata)
        metadata["relevance_score"] = score
        topic_terms = self._extract_topic_terms(topic)
        min_threshold = 0.28 if len(topic_terms) < 4 else 0.34
        if score < min_threshold:
            return False
        return True

    def _dedupe_ranked_results(self, results: List[Tuple[Dict[str, Any], str]]) -> List[Tuple[Dict[str, Any], str]]:
        """Deduplicate results by DOI/URL/title and keep the highest-scoring candidate."""
        best_by_key: Dict[str, Tuple[Dict[str, Any], str]] = {}
        for metadata, source in results:
            key = (
                str(metadata.get('doi') or '').lower().strip()
                or str(metadata.get('url') or '').lower().strip()
                or re.sub(r'\W+', '', str(metadata.get('title') or '').lower())
            )
            if not key:
                continue
            current = best_by_key.get(key)
            if current is None or metadata.get('relevance_score', 0.0) > current[0].get('relevance_score', 0.0):
                best_by_key[key] = (metadata, source)
        return sorted(best_by_key.values(), key=lambda item: item[0].get('relevance_score', 0.0), reverse=True)

    def _llm_rerank_relevance(
        self,
        topic: str,
        results: List[Tuple[Dict[str, Any], str]],
    ) -> List[Tuple[Dict[str, Any], str]]:
        """Use LLM as a semantic reranker, while keeping heuristic screening as the hard floor."""
        if not self.llm_model or len(results) <= 1:
            return results

        candidates = []
        for idx, (metadata, source) in enumerate(results[:8]):
            candidates.append({
                "idx": idx,
                "source": source,
                "title": metadata.get("title", ""),
                "journal": metadata.get("journal", ""),
                "publisher": metadata.get("publisher", ""),
                "abstract": str((metadata.get("abstract") or metadata.get("snippet") or ""))[:500],
                "doi": metadata.get("doi", ""),
                "url": metadata.get("url", ""),
                "heuristic_relevance": metadata.get("relevance_score", 0.0),
            })

        prompt = f"""You are screening academic citation candidates for topic relevance.

Topic: {topic}

Candidates (JSON):
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Task:
- Keep only candidates directly relevant to the topic.
- Prefer conceptually aligned academic literature.
- Reject obviously off-topic, noisy, or weakly related items.
- Use heuristic_relevance as a hint, not a command.

Return ONLY JSON:
{{
  "keep_indices": [0, 2],
  "reasoning": "brief explanation"
}}
"""

        try:
            response = self.llm_model.generate_content(
                prompt,
                generation_config={"temperature": 0.1, "max_output_tokens": 1024},
            )
            text = strip_markdown_json((getattr(response, 'text', '') or '').strip())
            assessment = RetrievalRelevanceAssessment.model_validate(json.loads(text))
            kept = [results[idx] for idx in assessment.keep_indices if 0 <= idx < len(results[:8])]
            if kept:
                return kept + [item for i, item in enumerate(results) if i >= 8]
        except Exception as e:
            logger.debug(f"LLM relevance reranking skipped; using heuristic order: {e}")

        return results

    def _report_progress(self, message: str, event_type: str = "search") -> None:
        """Report progress to callback if available."""
        if self.progress_callback:
            try:
                self.progress_callback(message, event_type)
            except Exception as e:
                logger.debug(f"Progress callback error: {e}")

    def _load_cache(self) -> Dict[str, Optional[Tuple[Dict[str, Any], str]]]:
        """
        Load citation cache from disk.

        Returns:
            Dict mapping topics to (metadata, source) tuples, list of tuples, or None
        """
        if not self.CACHE_FILE.exists():
            logger.info(f"No existing cache file found at {self.CACHE_FILE}")
            return {}

        try:
            with open(self.CACHE_FILE, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            logger.info(f"Loaded {len(cache_data)} cached citations from {self.CACHE_FILE}")

            # Convert JSON back to cache format
            cache = {}
            for topic, value in cache_data.items():
                if value is None:
                    cache[topic] = None
                elif isinstance(value, list) and len(value) == 2 and isinstance(value[0], dict):
                    # Old format: [metadata_dict, source_string] - single citation
                    cache[topic] = (value[0], value[1])
                elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], list):
                    # New format: [[metadata1, source1], [metadata2, source2], ...] - multiple citations
                    cache[topic] = [(item[0], item[1]) for item in value]
                else:
                    # Fallback for unexpected format
                    cache[topic] = (value[0], value[1]) if isinstance(value, list) and len(value) >= 2 else None

            return cache
        except Exception as e:
            logger.warning(f"Failed to load cache from {self.CACHE_FILE}: {e}")
            return {}

    def _save_cache(self) -> None:
        """
        Save citation cache to disk.

        Persists the in-memory cache to a JSON file for reuse across runs.
        """
        try:
            # Convert cache to JSON-serializable format
            cache_data = {}
            for topic, value in self.cache.items():
                if value is None:
                    cache_data[topic] = None
                elif isinstance(value, list):
                    if len(value) == 0:
                        # Empty list - treat as no results
                        cache_data[topic] = None
                    elif isinstance(value[0], tuple):
                        # List of (metadata, source) tuples - convert to list of [metadata, source] lists
                        cache_data[topic] = [[metadata, source] for metadata, source in value]
                    else:
                        # Unexpected format - skip
                        logger.warning(f"Unexpected cache format for topic '{topic}': {type(value[0])}")
                        cache_data[topic] = None
                elif isinstance(value, tuple) and len(value) == 2:
                    # Single (metadata, source) tuple - for backward compatibility
                    metadata, source = value
                    cache_data[topic] = [metadata, source]
                else:
                    # Unexpected format - skip
                    logger.warning(f"Unexpected cache format for topic '{topic}': {type(value)}")
                    cache_data[topic] = None

            with open(self.CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)

            logger.debug(f"Saved {len(cache_data)} citations to cache file {self.CACHE_FILE}")
        except Exception as e:
            logger.error(f"Failed to save cache to {self.CACHE_FILE}: {e}")

    def research_citation(self, topic: str) -> List[Citation]:
        """
        Research citations using parallel API calls.

        Args:
            topic: Topic or description to research

        Returns:
            List of Citation objects (may be empty if none found)
        """
        # Check cache first
        if topic in self.cache:
            cached = self.cache[topic]
            if cached is None:
                return []
            # Cache may store either single tuple or list of tuples
            if isinstance(cached, list):
                # List of (metadata, source) tuples
                cached_list = cached
            else:
                # Single (metadata, source) tuple - convert to list
                cached_list = [cached]

            citations = []
            for cached_metadata, cached_source in cached_list:
                if self.verbose:
                    safe_print(
                        f"    ✓ Cached: {cached_metadata.get('authors', ['Unknown'])[0] if cached_metadata.get('authors') else 'Unknown'} et al. ({cached_metadata.get('year', 'n.d.')}) [from {cached_source}]"
                    )
                citation = self._create_citation(cached_metadata, cached_source)
                if citation:
                    citations.append(citation)
            return citations


        topic_clean = self._sanitize_query(topic)
        if self.verbose:
                    safe_print(f"  🔍 Researching: {topic_clean[:70]}{'...' if len(topic_clean) > 70 else ''}")

        # Classify query and determine API chain
        api_chain = None
        if self.enable_smart_routing:
            classification = self.query_router.classify_and_route(topic_clean)
            self.metrics["queries_total"] += 1

            is_worthy, reason = self._is_query_execution_worthy(topic_clean, classification)
            if not is_worthy:
                rewritten_candidate = self._rewrite_query_for_execution(topic_clean, classification)
                rewritten_classification = self.query_router.classify_and_route(rewritten_candidate) if rewritten_candidate else classification
                rewritten_worthy, rewritten_reason = self._is_query_execution_worthy(rewritten_candidate, rewritten_classification) if rewritten_candidate else (False, "empty rewritten query")
                if rewritten_candidate and rewritten_worthy:
                    topic_clean = rewritten_candidate
                    classification = rewritten_classification
                else:
                    regenerated_candidate = self._regenerate_query_from_hint(topic_clean, classification)
                    regenerated_classification = self.query_router.classify_and_route(regenerated_candidate) if regenerated_candidate else classification
                    regenerated_worthy, regenerated_reason = self._is_query_execution_worthy(regenerated_candidate, regenerated_classification) if regenerated_candidate else (False, "empty regenerated query")
                    if regenerated_candidate and regenerated_worthy:
                        topic_clean = regenerated_candidate
                        classification = regenerated_classification
                    else:
                        self.metrics["queries_skipped"] += 1
                        self._append_trace({
                            "event": "query_skipped",
                            "query": topic,
                            "sanitized_query": topic_clean,
                            "query_quality": classification.query_quality,
                            "reason": reason or rewritten_reason or regenerated_reason or classification.rewrite_hint,
                        })
                        logger.info(f"Skipping low-quality query '{topic_clean[:80]}' ({reason or classification.rewrite_hint})")
                        self.cache[topic] = None
                        self._save_cache()
                        return []
            topic_clean = self._rewrite_query_for_execution(topic_clean, classification)
            post_worthy, post_reason = self._is_query_execution_worthy(topic_clean, classification)
            if not post_worthy:
                self.metrics["queries_skipped"] += 1
                self._append_trace({
                    "event": "query_skipped",
                    "query": topic,
                    "sanitized_query": topic_clean,
                    "query_quality": classification.query_quality,
                    "reason": post_reason,
                })
                self.cache[topic] = None
                self._save_cache()
                return []
            self.metrics["queries_executed"] += 1
            self._append_trace({
                "event": "query_routed",
                "query": topic,
                "sanitized_query": topic_clean,
                "query_type": classification.query_type,
                "query_confidence": classification.confidence,
                "query_quality": classification.query_quality,
                "api_chain": self._build_quality_aware_api_chain(classification, topic_clean),
                "rewrite_hint": classification.rewrite_hint,
            })
            api_chain = self._build_quality_aware_api_chain(classification, topic_clean)
            if self.verbose:
                safe_print(f"    📊 Query type: {classification.query_type} (confidence: {classification.confidence:.2f})")
                safe_print(f"    🧪 Query quality: {classification.query_quality}")
                if classification.rewrite_hint:
                    safe_print(f"    ↺ Rewrite hint: {classification.rewrite_hint}")
        else:
            # Use original fallback chain if smart routing disabled
            api_chain = [
                'crossref', 'openalex', 'semantic_scholar',
                'openaire', 'core', 'doaj'
            ]

        # When smart routing is disabled, keep broad fallback behavior.
        if not self.enable_smart_routing:
            for extra_api in ('openaire', 'core', 'doaj'):
                if extra_api not in api_chain:
                    api_chain.append(extra_api)

        # Filter out disabled APIs from chain (Day 1 Fix)
        enabled_chain = []
        for api_name in api_chain:
            if api_name == 'crossref' and not self.enable_crossref:
                continue
            if api_name == 'openalex' and not self.enable_openalex:
                continue
            if api_name == 'openaire' and not self.enable_openaire:
                continue
            if api_name == 'core' and not self.enable_core:
                continue
            if api_name == 'doaj' and not self.enable_doaj:
                continue
            if api_name == 'semantic_scholar' and not self._is_semantic_scholar_available():
                continue
            enabled_chain.append(api_name)

        api_chain = enabled_chain

        if self.verbose and api_chain:
            safe_print(f"    🔀 API chain: {' → '.join(api_chain)}")


        # Collect ALL valid results from API chain
        valid_results: List[Tuple[Dict[str, Any], str]] = []


        # Determine if we should use parallel queries
        # Use parallel for academic/journal queries where multiple academic APIs are in chain
        primary_academic_chain = [a for a in api_chain if a in ('crossref', 'openalex', 'openaire', 'core', 'doaj')]
        use_parallel = len(primary_academic_chain) >= 2 and self.enable_crossref

        if use_parallel:
            # Query ALL academic APIs in parallel for maximum source diversity
            parallel_apis = [api for api in primary_academic_chain if api in {'crossref', 'openalex', 'openaire', 'core', 'doaj'}]


            # Report progress for parallel search
            self._report_progress("Querying academic APIs in parallel...", "search")

            if self.verbose:
                apis_str = " + ".join([a.replace("_", " ").title() for a in parallel_apis])
                safe_print(f"    → Querying {apis_str} in parallel...", end=" ", flush=True)
            results: List[Tuple[Optional[Dict[str, Any]], str]] = []

            with ThreadPoolExecutor(max_workers=min(3, max(1, len(parallel_apis)))) as executor:
                futures = {
                    executor.submit(self._search_api, api, topic_clean): api
                    for api in parallel_apis
                }
                try:
                    for future in as_completed(futures, timeout=30):  # 30s timeout - balanced for Gemini
                        try:
                            result = future.result()
                            results.append(result)
                        except Exception as e:
                            api = futures[future]
                            logger.debug(f"Parallel {api} error: {e}")
                            results.append((None, api))
                except (TimeoutError, FuturesTimeoutError):
                    # Graceful degradation: use whatever results we have
                    logger.warning(f"Parallel query timeout - {len(results)} of {len(futures)} APIs responded")
                    # Collect any completed futures
                    for future, api in futures.items():
                        if future.done():
                            try:
                                result = future.result(timeout=0)
                                if result not in results:
                                    results.append(result)
                            except Exception:
                                pass

            # Collect ALL valid results (not just best one)
            for result_metadata, result_source in results:
                self.metrics["candidates_seen"] += 1
                normalized = normalize_citation_metadata(result_metadata)
                if normalized and (normalized.get('doi') or normalized.get('url')) and self._is_topic_result_relevant(topic_clean, normalized):
                    valid_results.append((normalized, result_source))
                    self.metrics["candidates_accepted"] += 1
                    self.metrics["provider_success"][result_source] = self.metrics["provider_success"].get(result_source, 0) + 1
                    self._append_trace({
                        "event": "citation_accepted",
                        "query": topic,
                        "provider": result_source,
                        "title": normalized.get("title", ""),
                        "relevance_score": normalized.get("relevance_score", 0.0),
                    })
                    # Update source usage count for logging
                    self.source_usage_count[result_source] = self.source_usage_count.get(result_source, 0) + 1
                elif normalized:
                    self.metrics["candidates_rejected"] += 1
                    self.metrics["provider_rejects"][result_source] = self.metrics["provider_rejects"].get(result_source, 0) + 1
                    self._append_trace({
                        "event": "citation_rejected",
                        "query": topic,
                        "provider": result_source,
                        "title": normalized.get("title", ""),
                        "relevance_score": normalized.get("relevance_score", 0.0),
                        "reason": "below_relevance_threshold_or_missing_identifier",
                    })

            # Semantic Scholar becomes supplemental only after primary academic providers
            if (
                not valid_results
                and 'semantic_scholar' in api_chain
                and self._is_semantic_scholar_available()
                and self._can_use_semantic_scholar_budget()
                and not self._is_chinese_query(topic_clean)
                and (not self.enable_smart_routing or classification.query_quality == 'high')
            ):
                try:
                    metadata = normalize_citation_metadata(self.semantic_scholar.search_paper(topic_clean))
                    if metadata and (metadata.get('doi') or metadata.get('url')) and self._is_topic_result_relevant(topic_clean, metadata):
                        valid_results.append((metadata, "Semantic Scholar"))
                        self.source_usage_count["Semantic Scholar"] = self.source_usage_count.get("Semantic Scholar", 0) + 1
                except Exception as e:
                    logger.error(f"Semantic Scholar supplemental error: {e}")


            valid_results = self._dedupe_ranked_results(valid_results)
            valid_results = self._llm_rerank_relevance(topic_clean, valid_results)
            if valid_results:
                if self.verbose:
                    sources_str = ", ".join([src for _, src in valid_results])
                    safe_print(f"✓ ({sources_str})")
            else:
                if self.verbose:
                    safe_print(f"✗")
        else:
            # Sequential fallback for industry queries or when parallel not applicable
            for api_name in api_chain:
                if api_name == 'crossref' and self.enable_crossref:
                    self._report_progress("Querying Crossref for peer-reviewed papers...", "search")
                    if self.verbose:
                        safe_print(f"    → Trying Crossref API...", end=" ", flush=True)
                    try:
                        metadata = normalize_citation_metadata(self.crossref.search_paper(topic_clean))
                        if metadata and (metadata.get('doi') or metadata.get('url')) and self._is_topic_result_relevant(topic_clean, metadata):
                            valid_results.append((metadata, "Crossref"))
                            self.source_usage_count["Crossref"] = self.source_usage_count.get("Crossref", 0) + 1
                            if self.verbose:
                                safe_print(f"✓")
                        else:
                            if self.verbose:
                                safe_print(f"✗")
                    except Exception as e:
                        if self.verbose:
                            safe_print(f"✗ Error: {e}")
                        logger.error(f"Crossref error: {e}")

                elif api_name == 'openalex' and self.enable_openalex:
                    self._report_progress("Searching OpenAlex (250M+ works)...", "search")
                    if self.verbose:
                        safe_print(f"    → Trying OpenAlex API...", end=" ", flush=True)
                    try:
                        metadata = normalize_citation_metadata(self.openalex.search_paper(topic_clean))
                        if metadata and (metadata.get('doi') or metadata.get('url')) and self._is_topic_result_relevant(topic_clean, metadata):
                            valid_results.append((metadata, "OpenAlex"))
                            self.source_usage_count["OpenAlex"] = self.source_usage_count.get("OpenAlex", 0) + 1
                            if self.verbose:
                                safe_print(f"✓")
                        else:
                            if self.verbose:
                                safe_print(f"✗")
                    except Exception as e:
                        if self.verbose:
                            safe_print(f"✗ Error: {e}")
                        logger.error(f"OpenAlex error: {e}")

                elif api_name == 'openaire' and self.enable_openaire:
                    self._report_progress("Searching OpenAIRE (open-access repositories)...", "search")
                    if self.verbose:
                        safe_print(f"    → Trying OpenAIRE API...", end=" ", flush=True)
                    try:
                        metadata = normalize_citation_metadata(self.openaire.search_paper(topic_clean))
                        if metadata and (metadata.get('doi') or metadata.get('url')) and self._is_topic_result_relevant(topic_clean, metadata):
                            valid_results.append((metadata, "OpenAIRE"))
                            self.source_usage_count["OpenAIRE"] = self.source_usage_count.get("OpenAIRE", 0) + 1
                            if self.verbose:
                                safe_print(f"✓")
                        else:
                            if self.verbose:
                                safe_print(f"✗")
                    except Exception as e:
                        if self.verbose:
                            safe_print(f"✗ Error: {e}")
                        logger.error(f"OpenAIRE error: {e}")

                elif api_name == 'core' and self.enable_core:
                    self._report_progress("Searching CORE (open-access works)...", "search")
                    if self.verbose:
                        safe_print(f"    → Trying CORE API...", end=" ", flush=True)
                    try:
                        metadata = normalize_citation_metadata(self.core.search_paper(topic_clean))
                        if metadata and (metadata.get('doi') or metadata.get('url')) and self._is_topic_result_relevant(topic_clean, metadata):
                            valid_results.append((metadata, "CORE"))
                            self.source_usage_count["CORE"] = self.source_usage_count.get("CORE", 0) + 1
                            if self.verbose:
                                safe_print(f"✓")
                        else:
                            if self.verbose:
                                safe_print(f"✗")
                    except Exception as e:
                        if self.verbose:
                            safe_print(f"✗ Error: {e}")
                        logger.error(f"CORE error: {e}")

                elif api_name == 'doaj' and self.enable_doaj:
                    self._report_progress("Searching DOAJ (open-access journals)...", "search")
                    if self.verbose:
                        safe_print(f"    → Trying DOAJ API...", end=" ", flush=True)
                    try:
                        metadata = normalize_citation_metadata(self.doaj.search_paper(topic_clean))
                        if metadata and (metadata.get('doi') or metadata.get('url')) and self._is_topic_result_relevant(topic_clean, metadata):
                            valid_results.append((metadata, "DOAJ"))
                            self.source_usage_count["DOAJ"] = self.source_usage_count.get("DOAJ", 0) + 1
                            if self.verbose:
                                safe_print(f"✓")
                        else:
                            if self.verbose:
                                safe_print(f"✗")
                    except Exception as e:
                        if self.verbose:
                            safe_print(f"✗ Error: {e}")
                        logger.error(f"DOAJ error: {e}")

            if (
                not valid_results
                and 'semantic_scholar' in api_chain
                and self._is_semantic_scholar_available()
                and self._can_use_semantic_scholar_budget()
                and not self._is_chinese_query(topic_clean)
                and (not self.enable_smart_routing or classification.query_quality == 'high')
            ):
                self._report_progress("Searching Semantic Scholar (supplemental)...", "search")
                if self.verbose:
                    safe_print(f"    → Trying Semantic Scholar API (supplemental)...", end=" ", flush=True)
                try:
                    metadata = normalize_citation_metadata(self.semantic_scholar.search_paper(topic_clean))
                    if metadata and (metadata.get('doi') or metadata.get('url')) and self._is_topic_result_relevant(topic_clean, metadata):
                        valid_results.append((metadata, "Semantic Scholar"))
                        self.source_usage_count["Semantic Scholar"] = self.source_usage_count.get("Semantic Scholar", 0) + 1
                        if self.verbose:
                            safe_print("✓")
                    else:
                        if self.verbose:
                            safe_print("✗")
                except Exception as e:
                    if self.verbose:
                        safe_print(f"✗ Error: {e}")
                    logger.error(f"Semantic Scholar error: {e}")

        valid_results = self._dedupe_ranked_results(valid_results)
        valid_results = self._llm_rerank_relevance(topic_clean, valid_results)

        # Try LLM as absolute last resort (not part of smart routing)
        if not valid_results and self.enable_llm_fallback:
            if self.verbose:
                safe_print(f"    → Trying LLM tool fallback...", end=" ", flush=True)
            try:
                metadata = normalize_citation_metadata(self._llm_research(topic_clean))
                if metadata and (metadata.get('doi') or metadata.get('url')):
                    valid_results.append((metadata, "LLM Fallback"))
                    self.source_usage_count["LLM Fallback"] = self.source_usage_count.get("LLM Fallback", 0) + 1
                    if self.verbose:
                        safe_print(f"✓")
                else:
                    if self.verbose:
                        safe_print(f"✗")
            except Exception as e:
                if self.verbose:
                    safe_print(f"✗ Error: {e}")
                logger.error(f"LLM fallback error: {e}")

        # Cache results (even if empty list)
        if valid_results:
            self.cache[topic] = valid_results
        else:
            self.cache[topic] = None

        # Persist cache to disk
        self._save_cache()

        # Convert to Citation objects
        citations = []
        if valid_results:
            for metadata, source in valid_results:
                citation = self._create_citation(metadata, source)
                if citation:
                    citations.append(citation)
                    if self.verbose:
                        # Check if preprint and show visible marker (Fix 3)
                        preprint_marker = " ⚠️ [PREPRINT]" if citation.source_type == "preprint" else ""
                        safe_print(f"    ✓ Found: {citation.authors[0]} et al. ({citation.year}) [from {source}]{preprint_marker}")
                        if citation.doi:
                            safe_print(f"      DOI: {citation.doi}")
                        elif citation.url:
                            safe_print(f"      URL: {citation.url}")

        if not citations and self.verbose:
            safe_print(f"    ✗ No citations found for: {topic[:70]}...")

        return citations

    def _create_citation(self, metadata: Dict[str, Any], source: Optional[str] = None) -> Optional[Citation]:
        """
        Create Citation object from metadata.

        Args:
            metadata: Paper metadata from API or LLM
            source: API source that found this citation (Crossref, Semantic Scholar, etc.)

        Returns:
            Citation object or None if validation fails
        """
        try:
            metadata = normalize_citation_metadata(metadata)
            if not metadata:
                return None

            # Validate required fields
            # For web sources, only title and URL are required
            # Academic sources need authors and year
            is_web_source = metadata.get("source_type") == "website"

            if is_web_source:
                # Web sources: require title + (URL or DOI)
                if not metadata.get("title"):
                    logger.debug(f"Invalid web source: missing title")
                    return None
                if not metadata.get("url") and not metadata.get("doi"):
                    logger.debug(f"Invalid web source: missing URL/DOI")
                    return None
                # Fill in missing academic fields for web sources
                if not metadata.get("authors"):
                    # Extract organization name for web sources (better than domain)
                    url = metadata.get("url", "")
                    if url:
                        from urllib.parse import urlparse
                        domain = urlparse(url).netloc.lower().replace('www.', '')
                        
                        # Map known domains to proper organization names
                        DOMAIN_TO_ORG = {
                            # Consulting firms
                            'mckinsey.com': 'McKinsey & Company',
                            'bcg.com': 'Boston Consulting Group',
                            'bain.com': 'Bain & Company',
                            'deloitte.com': 'Deloitte',
                            'pwc.com': 'PwC',
                            'kpmg.com': 'KPMG',
                            'ey.com': 'Ernst & Young',
                            'accenture.com': 'Accenture',
                            # Industry analysts
                            'gartner.com': 'Gartner',
                            'forrester.com': 'Forrester',
                            'idc.com': 'IDC',
                            'statista.com': 'Statista',
                            # International organizations
                            'who.int': 'World Health Organization',
                            'oecd.org': 'OECD',
                            'worldbank.org': 'World Bank',
                            'un.org': 'United Nations',
                            'imf.org': 'IMF',
                            'wto.org': 'World Trade Organization',
                            'unesco.org': 'UNESCO',
                            # US Government agencies
                            'nist.gov': 'NIST',
                            'nih.gov': 'NIH',
                            'cdc.gov': 'CDC',
                            'fda.gov': 'FDA',
                            'epa.gov': 'EPA',
                            'nasa.gov': 'NASA',
                            'nsf.gov': 'NSF',
                            'energy.gov': 'U.S. Department of Energy',
                            'state.gov': 'U.S. Department of State',
                            'whitehouse.gov': 'White House',
                            'congress.gov': 'U.S. Congress',
                            'gao.gov': 'GAO',
                            'cbo.gov': 'CBO',
                            # EU institutions
                            'europa.eu': 'European Commission',
                            'europarl.europa.eu': 'European Parliament',
                            'ecb.europa.eu': 'European Central Bank',
                            # Think tanks & research institutes
                            'brookings.edu': 'Brookings Institution',
                            'rand.org': 'RAND Corporation',
                            'cfr.org': 'Council on Foreign Relations',
                            'carnegieendowment.org': 'Carnegie Endowment',
                            'csis.org': 'CSIS',
                            'heritage.org': 'Heritage Foundation',
                            'aei.org': 'American Enterprise Institute',
                            'pewresearch.org': 'Pew Research Center',
                            'urban.org': 'Urban Institute',
                            'cato.org': 'Cato Institute',
                            # AI research centers
                            'cset.georgetown.edu': 'Georgetown CSET',
                            'hai.stanford.edu': 'Stanford HAI',
                            'ainowinstitute.org': 'AI Now Institute',
                            # Top universities (research centers)
                            'mit.edu': 'MIT',
                            'stanford.edu': 'Stanford University',
                            'harvard.edu': 'Harvard University',
                            'berkeley.edu': 'UC Berkeley',
                            'ox.ac.uk': 'University of Oxford',
                            'cam.ac.uk': 'University of Cambridge',
                            'princeton.edu': 'Princeton University',
                            'yale.edu': 'Yale University',
                            'columbia.edu': 'Columbia University',
                            'cmu.edu': 'Carnegie Mellon University',
                            # News & journalism
                            'reuters.com': 'Reuters',
                            'bbc.com': 'BBC',
                            'nytimes.com': 'New York Times',
                            'ft.com': 'Financial Times',
                            'economist.com': 'The Economist',
                            'wsj.com': 'Wall Street Journal',
                            # Tech giants (official research)
                            'openai.com': 'OpenAI',
                            'deepmind.com': 'DeepMind',
                            'anthropic.com': 'Anthropic',
                            'research.google': 'Google Research',
                            'ai.google': 'Google AI',
                            'research.microsoft.com': 'Microsoft Research',
                            'research.ibm.com': 'IBM Research',
                            'research.facebook.com': 'Meta AI',
                        }
                        
                        # Try exact match first, then suffix match for subdomains
                        org_name = DOMAIN_TO_ORG.get(domain)
                        if not org_name:
                            # Suffix match: energy.ec.europa.eu -> europa.eu -> European Commission
                            for known_domain, org in DOMAIN_TO_ORG.items():
                                if domain.endswith('.' + known_domain) or domain == known_domain:
                                    org_name = org
                                    break
                        
                        if org_name:
                            metadata["authors"] = [org_name]
                        else:
                            # REJECT unknown domains - don't use domain as author
                            # This prevents "issuu.com et al." citations
                            logger.debug(f"Rejecting web source: unknown org for domain '{domain}'")
                            return None
                    else:
                        metadata["authors"] = ["Web Source"]
                if not metadata.get("year"):
                    # Use current year for undated web sources
                    from datetime import datetime
                    metadata["year"] = datetime.now().year
            else:
                # Academic sources: require title + authors + year
                if not metadata.get("title") or not metadata.get("authors") or not metadata.get("year"):
                    logger.debug(f"Invalid metadata: missing required fields")
                    return None

            # Fix 4: Validate publication year
            year = metadata.get("year")
            if year:
                is_valid_year, year_reason, is_recent = validate_publication_year(year)
                if not is_valid_year:
                    logger.debug(f"Rejecting citation: invalid year {year} ({year_reason})")
                    return None
            
            # Fix 7: Validate author names for ALL sources (catches single-letter and generic names)
            authors = metadata.get("authors", [])
            if authors:
                first_author = authors[0] if authors else ""
                is_valid_author, author_reason = validate_author_name(first_author)
                if not is_valid_author:
                    logger.debug(f"Rejecting citation: invalid author '{first_author}' ({author_reason})")
                    return None
            
            # Check if this is a preprint (Fix 3)
            doi = metadata.get("doi", "")
            is_preprint = is_preprint_doi(doi)
            
            # Determine source_type (preprint overrides other types)
            source_type = metadata.get("source_type", "website")
            if is_preprint:
                source_type = "preprint"
            
            # Extract abstract/snippet (Gemini returns "snippet", others return "abstract")
            abstract = metadata.get("abstract") or metadata.get("snippet")

            inferred_language = metadata.get("language")
            if not inferred_language:
                title_text = str(metadata.get("title", ""))
                inferred_language = "chinese" if re.search(r'[\u4e00-\u9fff]', title_text) else "english"

            citation = Citation(
                citation_id="temp_id",  # Will be assigned by CitationCompiler
                authors=metadata["authors"],
                year=int(metadata["year"]),
                title=metadata["title"],
                source_type=source_type,
                language=inferred_language,
                journal=metadata.get("journal", ""),
                publisher=metadata.get("publisher", ""),
                volume=metadata.get("volume"),
                issue=metadata.get("issue"),
                pages=metadata.get("pages", ""),
                doi=doi,
                url=metadata.get("url", ""),
                api_source=source,  # Track which API found this citation
                abstract=abstract,  # Include abstract from API responses
                citation_count=metadata.get("citation_count"),
            )

            return citation

        except Exception as e:
            logger.error(f"Error creating citation: {e}")
            return None
    def _search_api(self, api_name: str, topic: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Search a single API for citations.

        Args:
            api_name: Name of the API ('crossref', 'openalex', 'semantic_scholar', 'openaire', 'core', 'doaj')
            topic: Topic to search for

        Returns:
            Tuple of (metadata, source_name) or (None, api_name)
        """
        try:
            query = self._sanitize_query(topic, provider=api_name)
            if not query:
                return (None, api_name)

            self.metrics["provider_calls"][api_name] = self.metrics["provider_calls"].get(api_name, 0) + 1

            logger.info(f"🔍 [{api_name.upper()}] Starting search for: {query[:80]}...")

            if api_name == 'crossref' and self.enable_crossref:
                logger.debug(f"  → Calling Crossref API...")
                metadata = self.crossref.search_paper(query)
                if metadata:
                    logger.info(
                        f"  ✓ Crossref found: {metadata.get('title', 'Unknown')[:80]}... (DOI: {metadata.get('doi', 'N/A')})"
                    )
                    return (metadata, "Crossref")
                else:
                    logger.debug(f"  ✗ Crossref returned no results")
            elif api_name == 'openalex' and self.enable_openalex:
                logger.debug(f"  → Calling OpenAlex API...")
                metadata = self.openalex.search_paper(query)
                if metadata:
                    logger.info(
                        f"  ✓ OpenAlex found: {metadata.get('title', 'Unknown')[:80]}... (DOI: {metadata.get('doi', 'N/A')})"
                    )
                    return (metadata, "OpenAlex")
                else:
                    logger.debug(f"  ✗ OpenAlex returned no results")
            elif api_name == 'semantic_scholar' and self.enable_semantic_scholar:
                logger.debug(f"  → Calling Semantic Scholar API...")
                metadata = self.semantic_scholar.search_paper(query)
                if metadata:
                    logger.info(
                        f"  ✓ Semantic Scholar found: {metadata.get('title', 'Unknown')[:80]}... (DOI: {metadata.get('doi', 'N/A')})"
                    )
                    return (metadata, "Semantic Scholar")
                else:
                    logger.debug(f"  ✗ Semantic Scholar returned no results")
            elif api_name == 'openaire' and self.enable_openaire:
                logger.debug(f"  → Calling OpenAIRE API...")
                metadata = self.openaire.search_paper(query)
                if metadata:
                    logger.info(
                        f"  ✓ OpenAIRE found: {metadata.get('title', 'Unknown')[:80]}... (URL: {metadata.get('url', 'N/A')[:50]})"
                    )
                    return (metadata, "OpenAIRE")
                else:
                    logger.debug(f"  ✗ OpenAIRE returned no results")
            elif api_name == 'core' and self.enable_core:
                logger.debug(f"  → Calling CORE API...")
                metadata = self.core.search_paper(query)
                if metadata:
                    logger.info(
                        f"  ✓ CORE found: {metadata.get('title', 'Unknown')[:80]}... (DOI: {metadata.get('doi', 'N/A')})"
                    )
                    return (metadata, "CORE")
                else:
                    logger.debug(f"  ✗ CORE returned no results")
            elif api_name == 'doaj' and self.enable_doaj:
                logger.debug(f"  → Calling DOAJ API...")
                metadata = self.doaj.search_paper(query)
                if metadata:
                    logger.info(
                        f"  ✓ DOAJ found: {metadata.get('title', 'Unknown')[:80]}... (DOI: {metadata.get('doi', 'N/A')})"
                    )
                    return (metadata, "DOAJ")
                else:
                    logger.debug(f"  ✗ DOAJ returned no results")
            return (None, api_name)

        except Exception as e:
            logger.error(
                f"❌ [{api_name.upper()}] Error during search: {type(e).__name__}: {str(e)[:100]}",
                exc_info=False,
            )
            return (None, api_name)

    def _pick_best_result(
        self, 
        results: List[Tuple[Optional[Dict[str, Any]], str]]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Pick the best result from multiple API responses with source variety.
        
        Uses round-robin source selection to ensure variety:
        - If multiple sources return valid results, prefer the least-used source
        - Still requires minimum quality (DOI or URL)
        
        Args:
            results: List of (metadata, source) tuples
            
        Returns:
            Best (metadata, source) tuple, or (None, None) if all failed
        """
        valid_results = [(m, s) for m, s in results if m is not None]
        
        if not valid_results:
            return (None, None)
        
        if len(valid_results) == 1:
            # Update usage count
            _, source = valid_results[0]
            self.source_usage_count[source] = self.source_usage_count.get(source, 0) + 1
            return valid_results[0]
        
        # Filter to only results with acceptable quality (DOI or URL)
        quality_results = [
            (m, s) for m, s in valid_results 
            if m.get('doi') or m.get('url')
        ]
        
        # If no quality results, fall back to any valid result
        if not quality_results:
            quality_results = valid_results
        
        # Sort by source usage count (ascending) to prefer least-used sources
        # This creates round-robin variety across all sources
        sorted_by_variety = sorted(
            quality_results,
            key=lambda x: self.source_usage_count.get(x[1], 0)
        )
        
        # Pick the least-used source
        metadata, source = sorted_by_variety[0]
        
        # Update usage count
        self.source_usage_count[source] = self.source_usage_count.get(source, 0) + 1
        
        return (metadata, source)



    def _llm_research(self, topic: str) -> Optional[Dict[str, Any]]:
        """
        Research citation using tool-backed LLM fallback.

        Strategy:
        1) Call internal research tools/APIs to gather evidence candidates with URLs
        2) Ask LLM to synthesize ONE best citation from those tool results
        3) Validate strict schema (including traceable source_urls)

        Args:
            topic: Topic to research

        Returns:
            Metadata dict or None
        """
        if not self.gemini_model:
            return None

        try:
            # -----------------------------------------------------------------
            # Step 1: tool-evidence gathering (internal tools/APIs)
            # -----------------------------------------------------------------
            candidate_apis: List[str] = []
            if self.enable_crossref:
                candidate_apis.append("crossref")
            if self.enable_openalex:
                candidate_apis.append("openalex")
            if self.enable_openaire:
                candidate_apis.append("openaire")
            if self.enable_core:
                candidate_apis.append("core")
            if self.enable_doaj:
                candidate_apis.append("doaj")
            if self._is_semantic_scholar_available():
                candidate_apis.append("semantic_scholar")

            tool_evidence: List[Dict[str, Any]] = []
            for api_name in candidate_apis:
                metadata, source_name = self._search_api(api_name, topic)
                normalized = normalize_citation_metadata(metadata)
                if not normalized:
                    continue
                url = (normalized.get("url") or "").strip()
                doi = (normalized.get("doi") or "").strip()
                trace_url = url or (f"https://doi.org/{doi}" if doi else "")
                if not trace_url:
                    continue

                tool_evidence.append(
                    {
                        "source": source_name,
                        "title": normalized.get("title", ""),
                        "authors": normalized.get("authors", []),
                        "year": normalized.get("year"),
                        "doi": doi,
                        "url": url,
                        "trace_url": trace_url,
                        "journal": normalized.get("journal", ""),
                        "publisher": normalized.get("publisher", ""),
                        "source_type": normalized.get("source_type", "journal"),
                    }
                )

            if not tool_evidence:
                logger.debug(f"LLM tool fallback skipped: no tool evidence for topic '{topic[:50]}...'")
                return None

            # Load Scout agent prompt
            from utils.agent_runner import load_prompt

            scout_prompt = load_prompt("prompts/01_research/scout.md")

            # -----------------------------------------------------------------
            # Step 2: LLM synthesis constrained by tool evidence
            # -----------------------------------------------------------------
            import json
            evidence_json = json.dumps(tool_evidence[:10], ensure_ascii=False)

            user_input = f"""# Research Task

Use ONLY the provided tool evidence to select ONE most relevant citation.
Do NOT invent fields and do NOT use knowledge outside the evidence.

**Topic:** {topic}

## Tool Evidence (JSON)
{evidence_json}

## Requirements (Strict)

1. Pick exactly ONE best citation from the tool evidence
2. Include traceable source URLs in `source_urls`
3. `source_urls` must come from the tool evidence `trace_url`/`url`
4. Return ONLY JSON

## Output Format

Return a JSON object with this structure:

```json
{{
  "authors": ["Author One", "Author Two"],
  "year": 2023,
  "title": "Complete Paper Title",
  "source_type": "journal|conference|book|report|article",
  "journal": "Journal Name (if journal article)",
  "conference": "Conference Name (if conference paper)",
  "doi": "10.xxxx/xxxxx (if available)",
  "url": "https://... (if available)",
  "pages": "1-10 (if available)",
  "volume": "5 (if available)",
  "publisher": "Publisher Name (if available)"
  "source_urls": ["https://..."]
}}
```

## Important

- Return ONLY the JSON object, no markdown, no explanation
- Ensure all fields are present (use empty string "" if not available)
- year must be an integer
- authors must be a list (even if only one author)
- source_type must be one of: journal, conference, book, report, article
- source_urls must include at least one valid http/https URL from tool evidence
- If you cannot find a paper, return: {{"error": "No paper found"}}
"""

            # Call model for tool-grounded synthesis
            # Note: Safety settings are handled by model/provider configuration.
            response = self.gemini_model.generate_content(
                [scout_prompt, user_input],
                generation_config={"temperature": 0.2, "max_output_tokens": 2048},
            )

            # -----------------------------------------------------------------
            # Step 3: schema validation + traceability checks
            # -----------------------------------------------------------------

            # Check if response was blocked by safety filter
            if not response.candidates:
                logger.warning(f"LLM response blocked (no candidates) for topic: {topic[:50]}...")
                return None

            candidate = response.candidates[0]
            if not is_stop_finish_reason(getattr(candidate, 'finish_reason', None)):
                logger.warning(
                    f"LLM response blocked (finish_reason={candidate.finish_reason}) for topic: {topic[:50]}..."
                )
                return None

            # Try to access response text safely
            try:
                response_text = response.text.strip()
            except ValueError as e:
                # response.text raises ValueError if no valid part exists
                logger.warning(f"LLM response has no valid text (safety filter likely) for topic: {topic[:50]}...")
                return None

            # Remove markdown code blocks if present
            response_text = strip_markdown_json(response_text)

            # Parse JSON first (error responses lack required fields for Pydantic)
            try:
                raw_data = json.loads(response_text)
            except json.JSONDecodeError as e:
                logger.warning(f"LLM returned invalid JSON for topic '{topic[:50]}...': {e}")
                logger.debug(f"Raw response: {response_text[:200]}...")
                return None

            # Check for error before validation
            if "error" in raw_data:
                logger.debug(f"LLM returned error response for topic '{topic[:50]}...': {raw_data['error']}")
                return None

            # Validate with Pydantic
            try:
                data = LLMToolCitationResponse.model_validate(raw_data)
            except ValidationError as e:
                logger.warning(f"LLM returned invalid citation for topic '{topic[:50]}...': {e}")
                return None

            allowed_urls = {
                str(item.get("trace_url", "") or "").strip()
                for item in tool_evidence
                if str(item.get("trace_url", "") or "").strip()
            }
            allowed_urls.update(
                {
                    str(item.get("url", "") or "").strip()
                    for item in tool_evidence
                    if str(item.get("url", "") or "").strip()
                }
            )

            traceable_urls = [u for u in data.source_urls if u in allowed_urls]
            if not traceable_urls:
                logger.warning(
                    f"LLM tool citation rejected for topic '{topic[:50]}...': "
                    f"no traceable source_urls overlap with tool evidence"
                )
                return None

            primary_url = (data.url or "").strip() or traceable_urls[0]

            # Convert validated model to dict for existing pipeline
            return {
                "title": data.title,
                "authors": data.authors,
                "year": data.year,
                "doi": data.doi,
                "url": primary_url,
                "journal": data.journal or data.conference,
                "publisher": data.publisher,
                "volume": data.volume,
                "issue": data.issue,
                "pages": data.pages,
                "source_type": data.source_type,
                "source_urls": traceable_urls,
                "confidence": 0.5,  # Lower confidence for LLM results
            }

        except Exception as e:
            logger.error(f"LLM research failed for topic '{topic[:50]}...': {e}")
            return None

    def close(self) -> None:
        """Close API clients."""
        if hasattr(self, "crossref"):
            self.crossref.close()
        if hasattr(self, "openalex"):
            self.openalex.close()
        if hasattr(self, "semantic_scholar"):
            self.semantic_scholar.close()
        if hasattr(self, "openaire"):
            self.openaire.close()
        if hasattr(self, "core"):
            self.core.close()
        if hasattr(self, "doaj"):
            self.doaj.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
