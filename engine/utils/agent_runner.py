#!/usr/bin/env python3
"""
ABOUTME: Production agent utilities for draft generation
ABOUTME: Core functions for model setup, agent execution, and citation research

Extracted from tests/test_utils.py for proper production use.
These are the essential utilities needed by draft_generator.py and modal_worker.py.
"""

import sys
import time
import logging
import os
import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable, Tuple, List, TYPE_CHECKING, Any, Dict, Literal
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError


@dataclass
class NormalizedInput:
    topic: str
    scope: Optional[str]
    language: Literal["zh", "en", "mixed"]
    level: str
    target_minimum: int


def normalize_research_input(
    topic: str,
    scope: Optional[str] = None,
    level: str = "research_paper",
    target_minimum: int = 50,
    max_chars: int = 500,
) -> NormalizedInput:
    """Lightweight, non-blocking input normalization for research planning.

    This deliberately does not judge feasibility or rewrite the topic into a
    fixed template; it only cleans whitespace, deduplicates topic/scope overlap,
    detects language, and trims extremely long input.
    """
    clean_topic = re.sub(r"\s+", " ", (topic or "").strip())[:max_chars].strip()
    clean_scope = re.sub(r"\s+", " ", (scope or "").strip()) if scope else None
    if clean_scope and clean_scope == clean_topic:
        clean_scope = None
    elif clean_scope and clean_topic and clean_scope in clean_topic:
        clean_scope = None
    elif clean_scope:
        clean_scope = clean_scope[:max_chars].strip()
    return NormalizedInput(
        topic=clean_topic,
        scope=clean_scope,
        language=_detect_input_language(clean_topic, clean_scope),
        level=level or "research_paper",
        target_minimum=max(1, int(target_minimum or 1)),
    )

# Safe print function that handles broken pipes and respects CLI quiet mode
def safe_print(*args, **kwargs):
    """Print wrapper that catches BrokenPipeError and respects CLI quiet mode."""
    # Check verbosity setting from orchestrator (CLI quiet mode)
    try:
        from utils.api_citations.orchestrator import _verbose_research
        if not _verbose_research:
            return  # Suppress in CLI quiet mode
    except ImportError:
        pass  # If orchestrator not available, continue normally

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


def _build_research_fallback_queries(topic: str, scope: Optional[str] = None) -> List[str]:
    """High-signal deterministic fallback queries for standard-mode degradation.

    Avoid appending meaningless numbers and low-value English suffixes to Chinese topics.
    """
    import re

    topic = (topic or "").strip()
    scope = (scope or "").strip()
    if not topic:
        return []

    queries: List[str] = []

    def add(q: str):
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q)

    is_chinese = bool(re.search(r'[\u4e00-\u9fff]', topic + scope))

    def _compress_keywords(text: str, max_terms: int = 5) -> str:
        zh = re.findall(r'[\u4e00-\u9fff]{2,}', text or "")
        en = re.findall(r'[A-Za-z][A-Za-z\-]{2,}', (text or "").lower())
        zh_stop = {"研究", "影响", "变化", "方式", "机制", "路径", "分析", "相关", "实证", "案例", "综述"}
        en_stop = {"study", "research", "impact", "effects", "analysis", "based", "using"}
        terms = [t for t in zh if t not in zh_stop] + [t for t in en if t not in en_stop]
        uniq: List[str] = []
        seen = set()
        for t in terms:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return " ".join(uniq[:max_terms]).strip() or text

    compressed = _compress_keywords(f"{topic} {scope}".strip())
    add(compressed)
    if scope and scope != topic:
        add(_compress_keywords(f"{topic} {scope}"))

    if is_chinese:
        semantic = _build_semantic_units_heuristic(topic=topic, scope=scope)
        for q in _compose_zh_queries_from_semantics(semantic):
            add(q)
        for q in _compose_en_bridge_queries_from_semantics(semantic):
            add(q)

    else:
        for suffix in ["empirical study", "literature review", "case study", "mechanism analysis", "systematic review"]:
            add(f"{compressed} {suffix}")

    return queries[:8 if is_chinese else 8]


def build_fast_research_queries(
    topic: str,
    scope: Optional[str] = None,
    academic_level: Optional[str] = None,
    llm_model: Optional[Any] = None,
) -> List[str]:
    """Fast Query Planner: small, language-pure first-round query set.

    Replaces default Deep Planner for normal draft generation. It intentionally
    does not generate strategy narratives, coverage estimates, or broad query
    matrices. First round is capped at 8 queries.
    """
    normalized = normalize_research_input(
        topic=topic,
        scope=scope,
        level=academic_level or "research_paper",
        target_minimum=1,
    )
    topic = normalized.topic
    scope = normalized.scope or ""
    if not topic:
        return []
    input_language = normalized.language
    if input_language == "mixed":
        zh_chars = len(re.findall(r'[\u4e00-\u9fff]', f"{topic} {scope}"))
        en_chars = len(re.findall(r'[A-Za-z]', f"{topic} {scope}"))
        input_language = "zh" if zh_chars >= en_chars else "en"

    def add_unique(items: List[str]) -> List[str]:
        out: List[str] = []
        for item in items:
            q = re.sub(r"\s+", " ", (item or "").strip())
            if q and q not in out:
                out.append(q)
        return out

    if input_language == "zh":
        semantic = _build_semantic_units_heuristic(topic=topic, scope=scope, llm_model=llm_model)
        queries = _compose_zh_queries_from_semantics(semantic) + _compose_en_bridge_queries_from_semantics(semantic)
        return validate_and_compress_queries(
            topic=topic,
            queries=add_unique(queries),
            input_language="zh",
            mode="fast",
        )

    # English/non-Chinese topics stay English-only.
    tokens = [
        t for t in re.findall(r'[A-Za-z][A-Za-z\-]{2,}', f"{topic} {scope}".lower())
        if t not in {"study", "research", "impact", "impacts", "changes", "change", "their", "and", "the", "era", "scope"}
    ]
    core = " ".join(dict.fromkeys(tokens[:8])).strip() or topic
    queries = add_unique([
        f"{core} empirical study",
        f"{core} systematic review",
        f"{core} literature review",
        f"{core} case study",
        f"{core} meta-analysis",
        f"{core} peer relationships empirical",
        f"{core} social connectedness",
        f"{core} mental health systematic review",
    ])
    return validate_and_compress_queries(
        topic=topic,
        queries=queries[:8],
        input_language="en",
        mode="fast",
    )


def _detect_input_language(topic: str, scope: Optional[str] = None) -> str:
    text = f"{topic or ''} {scope or ''}"
    has_zh = bool(re.search(r'[\u4e00-\u9fff]', text))
    has_en = bool(re.search(r'[A-Za-z]{3,}', text))
    if has_zh and has_en:
        return "mixed"
    if has_zh:
        return "zh"
    return "en"


def _build_semantic_units_heuristic(
    topic: str,
    scope: Optional[str] = None,
    llm_model: Optional[Any] = None,
) -> Dict[str, List[str]]:
    text = f"{topic or ''} {scope or ''}".strip()
    low_value_zh = {"研究", "背景下", "及其", "以及", "关于", "分析", "探讨", "时代", "视角", "问题"}
    low_value_en = {"study", "research", "analysis", "effect", "effects", "impact", "impacts", "based", "using", "under"}
    zh_terms = [t for t in re.findall(r'[\u4e00-\u9fff]{2,}', text) if t not in low_value_zh]
    zh_segments: List[str] = []
    for chunk in zh_terms:
        parts = re.split(r'及其|以及|与|和|及|、', chunk)
        for part in parts:
            cleaned = re.sub(r'(背景下|研究|分析|探讨)$', '', part).strip()
            if len(cleaned) >= 2 and cleaned not in low_value_zh and cleaned not in zh_segments:
                zh_segments.append(cleaned)
    en_terms = [t.lower() for t in re.findall(r'[A-Za-z][A-Za-z\-]{2,}', text)]
    en_terms = [t for t in en_terms if t not in low_value_en]
    uniq_zh = list(dict.fromkeys(zh_terms))
    uniq_en = list(dict.fromkeys(en_terms))

    def _strip_json_wrapper(raw: str) -> str:
        s = (raw or "").strip()
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
        return s.strip()

    if llm_model is not None and re.search(r'[\u4e00-\u9fff]', text):
        prompt = f"""You are an academic search query planner for Chinese research topics.

Given a Chinese research title, do NOT generate queries by appending method words to the full title.
First decompose the title into semantic units. This is not a fixed dictionary; extract units from the title meaning.

Title: {topic}
Scope: {scope or ''}

Return JSON only:
{{
  "context_terms": [],
  "object_terms": [],
  "core_concept_terms": [],
  "outcome_terms": [],
  "relation_terms": [],
  "method_terms": []
}}

Rules:
- Each item must be a short retrieval phrase.
- Remove low-value words such as “背景下”, “研究”, “分析” when they do not add search value.
- Do not invent unrelated domains.
- method_terms may include only research-type terms implied by the title or generally useful academic method terms."""
        try:
            resp = llm_model.generate_content(prompt)
            text_out = _strip_json_wrapper(getattr(resp, "text", "") or "")
            parsed = json.loads(text_out)
            def _norm(v: Any) -> List[str]:
                if not isinstance(v, list):
                    return []
                out: List[str] = []
                for item in v:
                    s = re.sub(r"\s+", " ", str(item or "").strip())
                    if s and s not in out:
                        out.append(s)
                return out[:5]
            llm_semantic = {
                "context_terms": _norm(parsed.get("context_terms")),
                "object_terms": _norm(parsed.get("object_terms")),
                "core_concept_terms": _norm(parsed.get("core_concept_terms")),
                "outcome_terms": _norm(parsed.get("outcome_terms")),
                "relation_terms": _norm(parsed.get("relation_terms")),
                "method_terms": _norm(parsed.get("method_terms")),
            }
            if any(llm_semantic.values()):
                return llm_semantic
        except Exception:
            pass

    all_terms = zh_segments + [t for t in uniq_zh if t not in zh_segments] + uniq_en
    relation_keywords = ["影响机制", "作用机制", "中介机制", "调节效应", "社会影响", "影响", "机制", "关系", "效应", "路径", "治理", "变迁", "作用", "relation", "mechanism"]
    relation_terms = [t for t in all_terms if any(x in t for x in relation_keywords)]
    method_terms = [t for t in all_terms if any(x in t for x in ["实证", "问卷", "访谈", "案例", "综述", "比较", "empirical", "review", "case"])]
    object_terms = [t for t in all_terms if t not in relation_terms and t not in method_terms]
    context_terms = object_terms[:2]
    core_terms = object_terms[1:4] if len(object_terms) > 1 else object_terms[:]
    outcome_terms = object_terms[2:5] if len(object_terms) > 2 else object_terms[:]
    explicit_relation_terms: List[str] = []
    for marker in ["影响机制", "作用机制", "中介机制", "调节效应", "社会影响", "影响", "机制", "关系", "效应", "路径", "变迁", "作用"]:
        if marker in text and marker not in explicit_relation_terms:
            explicit_relation_terms.append(marker)
    if explicit_relation_terms:
        relation_terms = explicit_relation_terms + [t for t in relation_terms if t not in explicit_relation_terms]
    elif not relation_terms and any(x in text for x in ["影响", "作用", "效应", "机制", "变迁", "关系"]):
        relation_terms = [x for x in ["社会影响", "影响机制", "影响", "机制", "变迁", "关系", "效应"] if x in text][:2]
    if not method_terms:
        method_terms = ["实证研究", "文献综述"] if re.search(r'[\u4e00-\u9fff]', text) else ["empirical study", "literature review"]
    if re.search(r'[\u4e00-\u9fff]', text):
        default_objects = ["青年", "劳动者", "求职者", "高校毕业生"]
        default_outcomes = ["行为意向", "职业选择", "就业预期", "社会影响"]
        default_relations = ["影响机制", "作用路径"]
        if not object_terms:
            object_terms = list(default_objects)
        else:
            object_terms = object_terms + [x for x in default_objects if x not in object_terms]
        if not context_terms:
            context_terms = object_terms[:1] or ["社会情境"]
        if not core_terms:
            core_terms = (object_terms[:1] + default_outcomes[:1])[:2]
        if not outcome_terms:
            outcome_terms = list(default_outcomes)
        else:
            outcome_terms = outcome_terms + [x for x in default_outcomes if x not in outcome_terms]
        if not relation_terms:
            relation_terms = list(default_relations)
        else:
            relation_terms = relation_terms + [x for x in default_relations if x not in relation_terms]

    return {
        "context_terms": context_terms[:4],
        "object_terms": object_terms[:4],
        "core_concept_terms": core_terms[:4],
        "outcome_terms": outcome_terms[:4],
        "relation_terms": relation_terms[:4],
        "method_terms": method_terms[:4],
    }


def _compose_zh_queries_from_semantics(semantic_units: Dict[str, List[str]]) -> List[str]:
    c = semantic_units.get("context_terms", [])
    p = semantic_units.get("object_terms", []) or semantic_units.get("population_terms", [])
    core = semantic_units.get("core_concept_terms", [])
    o = semantic_units.get("outcome_terms", [])
    r = semantic_units.get("relation_terms", [])
    m = semantic_units.get("method_terms", [])

    low_value = {"背景下", "研究", "分析", "探讨", "及其", "以及"}
    def clean_terms(items: List[str]) -> List[str]:
        out: List[str] = []
        for item in items or []:
            token = re.sub(r"\s+", " ", str(item or "").strip())
            if token and token not in low_value and token not in out:
                out.append(token)
        return out

    c, p, core, o, r, m = map(clean_terms, [c, p, core, o, r, m])
    if not core:
        core = (c + p + o)[:2]
    if len(p) < 2:
        p = p + [x for x in ["青年", "劳动者", "求职者", "高校毕业生"] if x not in p]
    if len(o) < 2:
        o = o + [x for x in ["行为意向", "职业选择", "就业预期", "社会影响"] if x not in o]
    if not r:
        r = ["影响机制", "作用路径"]
    if not c:
        c = core[:1] or p[:1]
    if not m:
        m = ["实证研究"]

    templates = [
        (c[:1] + core[:1] + r[:1]),
        (c[:1] + p[:1] + core[:1]),
        (c[:1] + p[1:2] + o[:1]),
        (core[:1] + o[:1] + r[:1]),
        (c[:1] + core[:1] + m[:1]),
        (p[2:3] + core[:1] + m[:1]),
    ]
    queries: List[str] = []
    for parts in templates:
        uniq = []
        for part in parts:
            token = (part or "").strip()
            if token and token not in uniq:
                uniq.append(token)
        if len(uniq) >= 2:
            q = re.sub(r"\s+", " ", " ".join(uniq)).strip()
            if q and q not in queries:
                queries.append(q)
        if len(queries) >= 6:
            break

    if not any(any(rel in q for rel in r) for q in queries) and r:
        rel_query_parts = [x for x in (c[:1] + core[:1] + r[:1]) if x]
        if len(rel_query_parts) >= 2:
            rel_query = " ".join(dict.fromkeys(rel_query_parts))
            if rel_query not in queries:
                queries.append(rel_query)

    object_coverage = {obj for obj in p if any(obj in q for q in queries)}
    for obj in p[:3]:
        if len(object_coverage) >= 2:
            break
        coverage_query_parts = [x for x in (c[:1] + [obj] + o[:1]) if x]
        if len(coverage_query_parts) >= 2:
            coverage_query = " ".join(dict.fromkeys(coverage_query_parts))
            if coverage_query not in queries:
                queries.append(coverage_query)
                object_coverage = {candidate for candidate in p if any(candidate in q for q in queries)}

    return queries[:6]


def _compose_en_bridge_queries_from_semantics(semantic_units: Dict[str, List[str]]) -> List[str]:
    english_terms: List[str] = []
    for key in ("context_terms", "object_terms", "core_concept_terms", "outcome_terms", "relation_terms"):
        for term in semantic_units.get(key, []) or []:
            if re.fullmatch(r"[A-Za-z][A-Za-z\- ]{2,}", str(term or "").strip()):
                english_terms.append(str(term).strip().lower())
    english_terms = list(dict.fromkeys(english_terms))
    if len(english_terms) >= 2:
        base = " ".join(english_terms[:4])
        return [f"{base} empirical study", f"{base} literature review"][:2]
    # No fixed topic dictionary here: a language-pure generic bridge is safer
    # than pseudo-English made from the Chinese title.
    return [
        "Chinese social science topic empirical study",
        "Chinese research topic literature review",
    ]


def validate_and_compress_queries(topic: str, queries: List[str], input_language: str, mode: str = "fast") -> List[str]:
    topic = (topic or "").strip()
    max_q = 6 if mode == "fast" else 8
    cleaned: List[str] = []
    topic_escaped = re.escape(topic)
    mechanical_patterns = [
        rf"^{topic_escaped}\s*(实证研究|文献综述|系统综述|案例研究|机制研究)$",
        rf"^{topic_escaped}\s*(empirical study|literature review|systematic review|case study|mechanism study)$",
    ]
    low_value_re = re.compile(r"(背景下\s*){2,}|(研究\s*){2,}|(分析\s*){2,}")
    for raw in queries or []:
        q = re.sub(r"\s+", " ", (raw or "").strip())
        if not q:
            continue
        if any(re.match(pat, q, flags=re.IGNORECASE) for pat in mechanical_patterns):
            continue
        if topic and q.count(topic) > 1:
            continue
        if low_value_re.search(q):
            continue
        has_zh = bool(re.search(r'[\u4e00-\u9fff]', q))
        has_en = bool(re.search(r'[A-Za-z]{3,}', q))
        if input_language == "en" and has_zh:
            continue
        if input_language == "zh" and has_zh and has_en:
            continue
        if has_en and re.search(r'[\u4e00-\u9fff]', q):
            continue
        tokens = re.findall(r'[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z\-]{2,}', q)
        lowered_tokens = [t.lower() for t in tokens]
        if len(lowered_tokens) != len(set(lowered_tokens)):
            continue
        if len(tokens) < 2:
            continue
        if q not in cleaned:
            cleaned.append(q)

    if input_language == "zh":
        zh = [q for q in cleaned if re.search(r'[\u4e00-\u9fff]', q)]
        en = [q for q in cleaned if q not in zh]
        semantic = _build_semantic_units_heuristic(topic, None)
        if len(zh) < 3:
            for q in _compose_zh_queries_from_semantics(semantic):
                if q not in zh:
                    zh.append(q)
                if len(zh) >= 4:
                    break
        if len(en) < 1:
            for q in _compose_en_bridge_queries_from_semantics(semantic):
                if not re.search(r'[\u4e00-\u9fff]', q) and q not in en:
                    en.append(q)
                if len(en) >= 1:
                    break
        relation_terms = semantic.get("relation_terms", []) or ["影响机制"]
        if not any(any(rel in q for rel in relation_terms) for q in zh):
            for q in _compose_zh_queries_from_semantics(semantic):
                if any(rel in q for rel in relation_terms) and q not in zh:
                    zh.append(q)
                    break
        if zh and not any(any(rel in q for rel in relation_terms) for q in zh[:5]):
            relation_query = next(
                (q for q in zh if any(rel in q for rel in relation_terms)),
                None,
            )
            if relation_query:
                zh = [relation_query] + [q for q in zh if q != relation_query]
        if mode == "fast":
            # Keep Chinese-first, but preserve at least one English bridge query
            # when available to support international-index coverage.
            if en:
                cleaned = (zh[:5] + en[:1])[:6]
            else:
                cleaned = zh[:6]
        else:
            cleaned = (zh[:6] + en[:3])[:8]
    else:
        cleaned = cleaned[:max_q]
    return cleaned


def _select_seed_papers(citations: List['Citation'], max_seed_papers: int = 5) -> List['Citation']:
    """Select high-relevance seed papers from first-pass accepted citations."""
    ranked = sorted(
        citations,
        key=lambda c: float(getattr(c, "relevance_score", 0.0) or 0.0),
        reverse=True,
    )
    seeds = [c for c in ranked if float(getattr(c, "relevance_score", 0.0) or 0.0) >= 0.65]
    return seeds[:max_seed_papers]


def _expand_from_seed_papers(
    researcher: Any,
    seeds: List['Citation'],
    topic: str,
    budget_seconds: int = 60,
    max_total_expanded: int = 50,
) -> List['Citation']:
    """Expand citation pool from seed papers if provider capabilities are available."""
    if not seeds:
        return []

    start = time.monotonic()
    expanded: List['Citation'] = []

    openalex = getattr(researcher, "openalex", None)
    if not openalex:
        return expanded

    get_referenced = getattr(openalex, "get_referenced_works", None)
    get_cited_by = getattr(openalex, "get_cited_by_works", None)
    get_related = getattr(openalex, "get_related_works", None)

    for seed in seeds:
        if len(expanded) >= max_total_expanded or (time.monotonic() - start) >= budget_seconds:
            break
        doi = (getattr(seed, "doi", "") or "").strip()
        if not doi:
            continue

        for fn in [get_referenced, get_cited_by, get_related]:
            if not callable(fn):
                continue
            try:
                items = fn(doi, limit=5) or []
            except Exception:
                items = []
            for metadata in items[:5]:
                try:
                    if not researcher._is_topic_result_relevant(topic, metadata):
                        continue
                    c = Citation(
                        citation_id=f"seed-{metadata.get('doi') or metadata.get('url') or metadata.get('title','')[:20]}",
                        authors=metadata.get("authors", ["Unknown"]),
                        year=metadata.get("year", 0),
                        title=metadata.get("title", ""),
                        source_type=metadata.get("source_type", "journal"),
                        journal=metadata.get("journal", ""),
                        doi=metadata.get("doi", ""),
                        url=metadata.get("url", ""),
                        abstract=metadata.get("abstract", ""),
                        api_source="OpenAlex",
                    )
                    c.relevance_score = float(metadata.get("relevance_score", 0.0) or 0.0)
                    if c.relevance_score >= 0.40:
                        expanded.append(c)
                except Exception:
                    continue
                if len(expanded) >= max_total_expanded:
                    break
    return expanded[:max_total_expanded]


def _cap_research_queries(queries: List[str], topic: str, parallel_workers: int) -> List[str]:
    """Cap deep-research query count to match execution capacity.

    Prevents huge plans (e.g., 90 queries) from running for hours in low-concurrency mode.
    """
    import re

    if not queries:
        return []

    is_chinese_topic = bool(re.search(r'[\u4e00-\u9fff]', topic or ""))
    if parallel_workers <= 1:
        limit = 12 if is_chinese_topic else 15
    elif parallel_workers <= 2:
        limit = 20
    else:
        limit = 30

    # Non-English topics need balanced bilingual coverage: enough source-language
    # queries for native coverage, enough English queries for international APIs.
    if is_chinese_topic:
        def _is_chinese_query(q: str) -> bool:
            return bool(re.search(r'[\u4e00-\u9fff]', q or ""))

        def _is_english_query(q: str) -> bool:
            return bool(re.search(r'[A-Za-z]{3,}', q or "")) and not _is_chinese_query(q)

        def _zh_score(q: str) -> int:
            return _score_research_query(q, topic, prefer_chinese=True)

        def _en_score(q: str) -> int:
            return _score_research_query(q, topic, prefer_chinese=False)

        ranked = sorted(queries, key=_zh_score, reverse=True)
        chinese_queries = [q for q in ranked if _is_chinese_query(q)]
        english_queries = sorted([q for q in ranked if _is_english_query(q)], key=_en_score, reverse=True)
        other_queries = [q for q in ranked if q not in chinese_queries and q not in english_queries]

        protected: List[str] = []

        def add_unique(items: List[str], max_take: Optional[int] = None) -> None:
            taken = 0
            for item in items:
                if item not in protected:
                    protected.append(item)
                    taken += 1
                    if max_take is not None and taken >= max_take:
                        break

        min_zh_total = min(limit, max(6, limit // 3))
        min_en_total = min(limit, max(4, limit // 4))
        add_unique(chinese_queries, min_zh_total)
        anchor_queries = _compose_zh_queries_from_semantics(_build_semantic_units_heuristic(topic=topic, scope=None))
        add_unique(anchor_queries, 5)
        add_unique(english_queries, min_en_total)
        add_unique(ranked, limit)
        add_unique(other_queries, limit)
        protected = protected[:limit]

        # Enforce: front half contains both native-language and English bridge queries.
        front_half = max(1, len(protected) // 2)
        required_front_zh = max(1, front_half // 3)
        required_front_en = max(1, front_half // 3)
        current_front_zh = sum(1 for q in protected[:front_half] if _is_chinese_query(q))
        current_front_en = sum(1 for q in protected[:front_half] if _is_english_query(q))
        if current_front_zh < required_front_zh:
            remaining_zh = [q for q in chinese_queries if q not in protected[:front_half]]
            tail_non_zh_idx = [i for i, q in enumerate(protected[:front_half]) if not _is_chinese_query(q)]
            for idx, zh_q in zip(reversed(tail_non_zh_idx), remaining_zh):
                protected[idx] = zh_q
                current_front_zh += 1
                if current_front_zh >= required_front_zh:
                    break
        if current_front_en < required_front_en:
            remaining_en = [q for q in english_queries if q not in protected[:front_half]]
            tail_non_en_idx = [i for i, q in enumerate(protected[:front_half]) if not _is_english_query(q)]
            for idx, en_q in zip(reversed(tail_non_en_idx), remaining_en):
                protected[idx] = en_q
                current_front_en += 1
                if current_front_en >= required_front_en:
                    break
        return protected[:limit]

    capped = queries[:limit]
    return capped


def _rebalance_queries_for_academic_level(
    queries: List[str],
    academic_level: Optional[str],
    limit: Optional[int] = None,
) -> List[str]:
    """Rebalance query mix toward academic intent for lighter paper levels.

    For research papers, avoid over-indexing on policy/consulting-style queries.
    """
    if not queries:
        return []

    level = (academic_level or "").lower().strip()
    if level != "research_paper":
        return queries if limit is None else queries[:limit]

    budget = limit or len(queries)
    industry_keywords = [
        "mckinsey", "gartner", "bcg", "white paper", "consulting", "think tank",
        "who", "oecd", "nist", "ieee", "regulation", "regulatory", "framework",
        "guideline", "guidelines", "policy", "standards", "commission",
    ]
    academic_keywords = [
        "peer-reviewed", "systematic review", "meta-analysis", "empirical",
        "literature review", "journal", "conference", "dissertation", "factor productivity",
    ]

    def _is_industry_query(q: str) -> bool:
        ql = (q or "").lower()
        return any(k in ql for k in industry_keywords)

    def _academic_score(q: str) -> int:
        ql = (q or "").lower()
        score = 0
        score += sum(2 for k in academic_keywords if k in ql)
        score -= sum(1 for k in industry_keywords if k in ql)
        return score

    academic_queries = [q for q in queries if not _is_industry_query(q)]
    industry_queries = [q for q in queries if _is_industry_query(q)]

    academic_queries = sorted(academic_queries, key=_academic_score, reverse=True)
    industry_queries = sorted(industry_queries, key=_academic_score, reverse=True)

    # Keep industry queries capped for research_paper mode.
    max_industry = max(2, budget // 5)  # <=20%
    picked: List[str] = []
    picked.extend(academic_queries[:budget])
    remaining = max(0, budget - len(picked))
    if remaining > 0:
        picked.extend(industry_queries[:min(remaining, max_industry)])

    # If still short, only backfill with remaining non-industry queries.
    if len(picked) < budget:
        for q in queries:
            if q in picked or _is_industry_query(q):
                continue
            picked.append(q)
            if len(picked) >= budget:
                break
    return picked[:budget]


def _is_preprint_citation(citation: 'Citation') -> bool:
    """Heuristic preprint detection from metadata."""
    source_type = (getattr(citation, "source_type", "") or "").lower()
    doi = (getattr(citation, "doi", "") or "").lower()
    url = (getattr(citation, "url", "") or "").lower()
    publisher = (getattr(citation, "publisher", "") or "").lower()
    title = (getattr(citation, "title", "") or "").lower()
    preprint_markers = [
        "preprint", "arxiv", "biorxiv", "medrxiv", "ssrn", "osf",
        "10.48550/", "10.2139/", "10.31219/",
    ]
    if source_type == "preprint":
        return True
    hay = " ".join([doi, url, publisher, title])
    return any(marker in hay for marker in preprint_markers)


def _build_chinese_coverage_rescue_queries(topic: str, scope: Optional[str] = None) -> List[str]:
    """High-signal Chinese rescue queries used when zh coverage is insufficient."""
    topic = (topic or "").strip()
    scope = (scope or "").strip()
    if not topic:
        return []

    queries: List[str] = []

    def add(q: str) -> None:
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q)

    def _compress(text: str, max_terms: int = 5) -> str:
        terms = re.findall(r'[\u4e00-\u9fff]{2,}', text or "")
        stop = {"研究", "影响", "变化", "方式", "机制", "路径", "分析", "相关", "实证", "案例", "综述"}
        kept: List[str] = []
        for term in terms:
            if term in stop or term in kept:
                continue
            kept.append(term)
            if len(kept) >= max_terms:
                break
        return " ".join(kept).strip() or topic

    semantic = _build_semantic_units_heuristic(topic=topic, scope=scope)
    for q in _compose_zh_queries_from_semantics(semantic):
        add(q)
    compressed = _compress(f"{topic} {scope}".strip())
    if compressed and compressed not in queries:
        add(compressed)

    return queries[:5]


def _score_research_query(query: str, topic: str, prefer_chinese: bool = False) -> int:
    q = (query or "").strip()
    ql = q.lower()
    score = 0
    has_zh = bool(re.search(r'[\u4e00-\u9fff]', q))
    has_en = bool(re.search(r'[A-Za-z]{3,}', q))
    if prefer_chinese and has_zh:
        score += 8
    if not prefer_chinese and has_en:
        score += 4

    high_signal = [
        "实证", "实证研究", "机制", "路径", "案例", "文献综述", "面板数据", "计量",
        "empirical", "systematic review", "literature review", "case study", "firm-level", "mechanism",
    ]
    domain_terms: List[str] = []
    weak_terms = [
        "创新", "生态系统", "方式", "提升", "增强", "促进", "优化", "改变",
        "innovation", "ecosystem", "transformation", "improve", "enhance", "promote",
    ]
    bad_terms = ["白皮书", "报告", "指南", "framework", "policy", "white paper", "report"]

    score += sum(4 for kw in high_signal if kw in ql or kw in q)
    score += sum(2 for kw in domain_terms if kw in ql or kw in q)
    score -= sum(2 for kw in weak_terms if kw in ql or kw in q)
    score -= sum(2 for kw in bad_terms if kw in ql or kw in q)

    if has_zh and has_en:
        # mixed language is okay only as bilingual bridge, not as malformed mixed sentence
        score -= 1
    if len(q.split()) > 12 or len(q) > 80:
        score -= 2
    if len(q.split()) < 2 and len(q) < 8:
        score -= 3

    topic_tokens = [t for t in re.findall(r'[A-Za-z][A-Za-z\-]{3,}|[\u4e00-\u9fff]{2,}', topic or "")]
    overlap = sum(1 for t in topic_tokens[:6] if t.lower() in ql or t in q)
    score += overlap
    return score


def _prioritize_research_queries(queries: List[str], topic: str, academic_level: Optional[str], parallel_workers: int) -> List[str]:
    if not queries:
        return []
    is_chinese_topic = bool(re.search(r'[\u4e00-\u9fff]', topic or ""))
    ranked = sorted(
        list(dict.fromkeys(q.strip() for q in queries if (q or '').strip())),
        key=lambda q: _score_research_query(q, topic, prefer_chinese=is_chinese_topic),
        reverse=True,
    )
    ranked = _cap_research_queries(ranked, topic, parallel_workers)
    ranked = _rebalance_queries_for_academic_level(ranked, academic_level=academic_level, limit=len(ranked))
    if (academic_level or "").lower().strip() == "research_paper":
        # Research-paper runs should be fast and focused. Oversized planner output
        # causes many low-yield API calls and can turn a 5-10 minute phase into
        # a 30+ minute failure. Keep this configurable for heavy users.
        cap = int(os.getenv("SCOUT_RESEARCH_PAPER_QUERY_CAP", "16"))
        ranked = ranked[:max(1, cap)]
    return ranked


def _build_quality_rescue_queries(topic: str, scope: Optional[str] = None, is_chinese_topic: bool = False) -> List[str]:
    """Build language-pure, database-friendly rescue queries."""
    topic = (topic or "").strip()
    scope = (scope or "").strip()
    if not topic:
        return []

    queries: List[str] = []

    def add(q: str) -> None:
        q = (q or "").strip()
        if q and q not in queries:
            queries.append(q)

    compressed_text = " ".join(re.findall(r'[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z\-]{2,}', f"{topic} {scope}".strip()))
    compressed_text = compressed_text.strip() or topic

    if is_chinese_topic:
        semantic = _build_semantic_units_heuristic(topic=topic, scope=scope)
        for q in _compose_zh_queries_from_semantics(semantic):
            add(q)
        if compressed_text and compressed_text not in queries:
            add(compressed_text)
    else:
        for suffix in ["empirical study", "systematic review", "literature review", "case study", "mechanism analysis", "comparative study"]:
            add(f"{compressed_text} {suffix}")
        if scope and scope != topic:
            add(f"{compressed_text} empirical study")

    return queries[:8]


def _build_research_paper_topup_queries(topic: str, scope: Optional[str] = None) -> List[str]:
    """Focused English academic top-up queries for research-paper Scout runs."""
    text = f"{topic or ''} {scope or ''}".strip()
    tokens = [
        t for t in re.findall(r'[A-Za-z][A-Za-z\-]{2,}', text.lower())
        if t not in {"study", "research", "impact", "impacts", "changes", "change", "era", "their", "and", "the"}
    ]
    core = " ".join(dict.fromkeys(tokens[:8])).strip() or (topic or "digital platforms youth social interaction")
    domain_templates = [
        "youth digital platforms social interaction systematic review",
        "adolescent social media friendship quality",
        "{core} systematic review",
        "{core} empirical study",
        "digital media use adolescent social connectedness",
        "online offline social interaction adolescents",
        "social media adolescent loneliness systematic review",
        "social media use adolescent peer relationships empirical",
        "digital communication youth social skills empirical",
        "adolescent social media mental health meta-analysis",
    ]
    queries: List[str] = []
    for tmpl in domain_templates:
        q = tmpl.format(core=core).strip()
        if q and q not in queries:
            queries.append(q)
    return queries[:10]


def _dedupe_citations(citations: List['Citation']) -> List['Citation']:
    """Deduplicate citations by DOI/URL/title while preserving first-seen order."""
    deduped: List['Citation'] = []
    seen = set()
    for citation in citations or []:
        key = (
            (getattr(citation, 'doi', '') or '').strip().lower()
            or (getattr(citation, 'url', '') or '').strip().lower()
            or ''.join(ch for ch in (getattr(citation, 'title', '') or '').lower() if ch.isalnum())
        )
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(citation)
    return deduped


def _compute_unique_source_breakdown(citations: List['Citation']) -> Dict[str, int]:
    """Compute source breakdown from final unique citations only."""
    breakdown: Dict[str, int] = {
        "Crossref": 0,
        "OpenAlex": 0,
        "OpenAIRE": 0,
        "CORE": 0,
        "DOAJ": 0,
        "Semantic Scholar": 0,
        "LLM Fallback": 0,
    }
    for citation in citations or []:
        source = getattr(citation, "api_source", None) or "Unknown"
        if source in breakdown:
            breakdown[source] += 1
    return breakdown


def _classify_research_quality_failure(
    raw_candidates_count: int,
    normalized_candidates_count: int,
    accepted_candidates_count: int,
    valid_citations_count: int,
    relevance_pass_rate: float,
    required_threshold: float,
    target_minimum: int,
    preprint_ratio: float = 0.0,
    max_preprint_ratio: float = 1.0,
) -> Optional[str]:
    """Classify Scout quality outcomes without conflating candidates and citations."""
    if (
        raw_candidates_count == 0
        and normalized_candidates_count == 0
        and accepted_candidates_count == 0
        and valid_citations_count == 0
    ):
        return "retrievability_failure"
    if valid_citations_count > 0 and relevance_pass_rate < required_threshold:
        return "relevance_quality_failure"
    if valid_citations_count > 0 and valid_citations_count < target_minimum:
        return "insufficient_citation_count"
    if valid_citations_count >= target_minimum and preprint_ratio > max_preprint_ratio:
        return "source_quality_warning"
    return None


def _build_research_diagnostics(
    topic: str,
    scope: Optional[str],
    planned_queries: List[str],
    research_metrics: Dict[str, Any],
    sources_breakdown: Dict[str, int],
    citation_count: int,
    final_count: int,
    failure_type: Optional[str],
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    zh_query_count = sum(1 for q in planned_queries if re.search(r'[\u4e00-\u9fff]', q or ""))
    en_query_count = sum(1 for q in planned_queries if re.search(r'[A-Za-z]{3,}', q or "") and not re.search(r'[\u4e00-\u9fff]', q or ""))
    return {
        "planner": {
            "input_language": _detect_input_language(topic or "", scope),
            "query_count": len(planned_queries),
            "zh_query_count": zh_query_count,
            "en_query_count": en_query_count,
            "mechanical_queries_removed": int(research_metrics.get("mechanical_queries_removed", 0) or 0),
            "mixed_language_queries_removed": int(research_metrics.get("mixed_language_queries_removed", 0) or 0),
            "duplicate_queries_removed": int(research_metrics.get("duplicate_queries_removed", 0) or 0),
        },
        "retrieval": {
            "raw_candidates": int(research_metrics.get("candidates_seen_raw", 0) or 0),
            "normalized_candidates": int(research_metrics.get("normalized_candidates", 0) or 0),
            "accepted_candidates": int(research_metrics.get("candidates_accepted", 0) or 0),
            "valid_citations": citation_count,
            "providers_used": [k for k, v in (sources_breakdown or {}).items() if v > 0],
            "provider_errors": research_metrics.get("provider_errors", {}) or {},
            "executed_zh_queries": zh_query_count,
            "executed_en_queries": en_query_count,
        },
        "quality": {
            "relevance_pass_rate": float(research_metrics.get("relevance_pass_rate", 0.0) or 0.0),
            "final_count": final_count,
            "failure_type": failure_type,
            "warnings": warnings or [],
        },
    }

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_config
from concurrency.concurrency_config import get_concurrency_config
from utils.output_validators import ValidationResult
from utils.api_citations.orchestrator import CitationResearcher
from utils.citation_database import Citation
from utils.llm_provider import (
    create_llm_model,
    is_stop_finish_reason,
    is_function_call_finish_reason,
)
from utils.deep_research import DeepResearchPlanner
from utils.token_tracker import CallStatus

# Configure logging
logger = logging.getLogger(__name__)


def _count_chinese_language_hits(citations: List[Citation]) -> Dict[str, int]:
    """Count Chinese-language coverage in collected citations.

    Uses title language as the primary signal and keeps a secondary signal for
    citations explicitly marked as Chinese language metadata.
    """
    import re

    title_zh_hits = 0
    language_zh_hits = 0

    for citation in citations or []:
        title = (getattr(citation, "title", "") or "").strip()
        language = (getattr(citation, "language", "") or "").lower().strip()

        if re.search(r'[\u4e00-\u9fff]', title):
            title_zh_hits += 1
        if language in {"zh", "zh-cn", "zh-tw", "chinese", "中文"}:
            language_zh_hits += 1

    return {
        "title_zh_hits": title_zh_hits,
        "language_zh_hits": language_zh_hits,
        "effective_zh_hits": max(title_zh_hits, language_zh_hits),
    }


def _count_chinese_primary_hits(citations: List[Citation]) -> Dict[str, int]:
    """Backward-compatible counter for CNKI/Baidu Scholar primary hits.

    Kept for diagnostics/tests compatibility. New quality gates should use
    `_count_chinese_language_hits` instead.
    """
    cnki_hits = 0
    baidu_scholar_hits = 0

    for citation in citations or []:
        publisher = (getattr(citation, "publisher", "") or "").lower()
        url = (getattr(citation, "url", "") or "").lower()

        is_cnki = (
            "cnki" in publisher
            or "中国知网" in publisher
            or "cnki.net" in url
            or "oversea.cnki.net" in url
            or "kns.cnki.net" in url
        )
        is_baidu = (
            "baidu scholar" in publisher
            or "xueshu.baidu" in publisher
            or "xueshu.baidu.com" in url
        )

        if is_cnki:
            cnki_hits += 1
        elif is_baidu:
            baidu_scholar_hits += 1

    return {
        "cnki_hits": cnki_hits,
        "baidu_scholar_hits": baidu_scholar_hits,
        "primary_hits": cnki_hits + baidu_scholar_hits,
    }


def setup_model(model_override: Optional[str] = None) -> Any:
    """
    Initialize and return configured LLM model wrapper.

    Args:
        model_override: Optional model name to override config default

    Returns:
        Model wrapper with generate_content() method

    Raises:
        ValueError: If API key is missing or model name is invalid
    """
    config = get_config()
    config.validate_api_keys()
    return create_llm_model(model_override=model_override)


def load_prompt(prompt_path: str) -> str:
    """
    Load agent prompt from markdown file.

    Args:
        prompt_path: Path to prompt file (relative to project root or absolute)

    Returns:
        str: Content of the prompt file

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    config = get_config()
    path = Path(prompt_path)

    # If relative path, try relative to project root
    if not path.is_absolute():
        path = config.paths.project_root / path

    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def run_agent(
    model: Any,
    name: str,
    prompt_path: str,
    user_input: str,
    save_to: Optional[Path] = None,
    verbose: bool = True,
    validators: Optional[List[Callable[[str], ValidationResult]]] = None,
    max_retries: int = 3,
    skip_validation: bool = False,
    token_tracker: Optional[Any] = None,
    token_stage: Optional[str] = None,
) -> str:
    """
    Run an AI agent with given prompt and input, with optional validation.

    This function is the core execution layer for all agents in the draft pipeline.
    It handles LLM interaction, output validation, retries, and file I/O.

    Args:
        model: Configured model instance
        name: Human-readable name for the agent (for logging)
        prompt_path: Path to agent prompt file
        user_input: User's request/input for the agent
        save_to: Optional path to save output
        verbose: Whether to print progress messages
        validators: Optional list of validation functions to apply to output
        max_retries: Maximum retry attempts if validation fails (default: 3)
        skip_validation: If True, skip all validation checks (for automated runs)

    Returns:
        str: Validated agent output text

    Raises:
        Exception: If agent execution fails or validation fails after all retries
    """
    # Override validators if skip_validation is True
    if skip_validation:
        validators = None
        logger.info(f"Agent '{name}': Validation skipped (skip_validation=True)")
    if verbose:
        safe_print(f"\n{'='*70}")
        safe_print(f"🤖 {name}")
        safe_print(f"{'='*70}")

    # Load agent prompt
    agent_prompt = load_prompt(prompt_path)

    # Combine agent prompt with user input
    full_prompt = f"{agent_prompt}\n\n---\n\nUser Request:\n{user_input}"

    logger.debug(f"Agent '{name}': Starting execution")
    logger.debug(f"Prompt length: {len(full_prompt)} chars")
    logger.debug(f"Validators: {len(validators) if validators else 0}")

    # Initialize output variable with explicit type
    output: str = ""

    # Empty loop detection (V3 feature): exit early if model produces empty output repeatedly
    consecutive_empty_outputs = 0
    MAX_CONSECUTIVE_EMPTY = 3

    # Retry loop with exponential backoff
    for attempt in range(max_retries):
        if verbose and attempt > 0:
            safe_print(f"Retry attempt {attempt}/{max_retries}...", end=' ', flush=True)
        elif verbose:
            safe_print("Generating...", end=' ', flush=True)

        start_time = time.time()

        try:
            # Generate LLM response
            # Note: If model tools (web search/URL context) hit rate limits,
            # we catch the exception and use fallbacks (DataForSEO/OpenPull)
            try:
                response = model.generate_content(full_prompt)
            except Exception as tool_error:
                error_str = str(tool_error)
                # Check if it's a tool rate limit error (429)
                if "429" in error_str or "rate limit" in error_str.lower() or "quota" in error_str.lower():
                    logger.warning(f"Agent '{name}': model tools rate limited - will retry without tools")
                    # Retry without tools (fallback to direct generation)
                    # Note: For web search/URL context, we'd need to manually call fallbacks
                    # This is a simplified retry - full fallback integration would require
                    # detecting which tool failed and calling appropriate fallback
                    response = model.generate_content(full_prompt)
                else:
                    raise  # Re-raise if not a rate limit error
            
            # Handle function calls and other edge cases
            # Check if response has candidates
            if not response.candidates:
                raise ValueError(f"Agent '{name}': No candidates in response (likely blocked)")
            
            candidate = response.candidates[0]
            finish_reason = getattr(candidate, 'finish_reason', None)
            
            # Check what parts exist in the response BEFORE accessing response.text
            # This prevents ValueError when response has function calls or no text parts
            has_text_part = False
            has_function_call = False
            
            if hasattr(candidate, 'content') and candidate.content:
                for part in candidate.content.parts:
                    if hasattr(part, 'text') and part.text:
                        has_text_part = True
                        break
                    if hasattr(part, 'function_call'):
                        has_function_call = True
            
            # If no text part exists, try to extract from parts directly
            output = None
            
            if has_text_part:
                # Safe to use response.text when text part exists
                try:
                    output = str(response.text)
                except ValueError:
                    # Fallback: extract from parts
                    if hasattr(candidate, 'content') and candidate.content:
                        text_parts = [part.text for part in candidate.content.parts if hasattr(part, 'text') and part.text]
                        if text_parts:
                            output = ''.join(text_parts)
            elif has_function_call:
                # Function call detected but no text - this is an error
                raise ValueError(
                    f"Agent '{name}': Response contains function call but no text content. "
                    f"Finish reason: {finish_reason}. "
                    f"This may indicate the model attempted to call a function incorrectly. "
                    f"Try regenerating or check if tools are properly configured."
                )
            else:
                # No text part and no function call - check finish_reason
                if is_stop_finish_reason(finish_reason):  # STOP/UNSPECIFIED but no text
                    raise ValueError(
                        f"Agent '{name}': Response has finish_reason={finish_reason} (STOP-like) but no text parts. "
                        f"This may indicate an empty response or safety filter block."
                    )
                elif is_function_call_finish_reason(finish_reason):
                    raise ValueError(
                        f"Agent '{name}': Response has finish_reason={finish_reason} (FUNCTION_CALL-like) but no text content. "
                        f"This indicates an invalid function call attempt."
                    )
                else:
                    raise ValueError(
                        f"Agent '{name}': No text content in response. "
                        f"Finish reason: {finish_reason}. "
                        f"Response may be blocked or empty."
                    )
            
            if not output:
                raise ValueError(f"Agent '{name}': Unable to extract text from response")
            
            output = str(output)

            # Defense-in-depth: scrub planning preambles, metadata, and cite_MISSING markers
            from utils.text_utils import clean_agent_output
            output = clean_agent_output(output)

            # Empty loop detection (V3 feature): track consecutive empty/trivial outputs
            if len(output.strip()) < 50:  # Effectively empty or trivial
                consecutive_empty_outputs += 1
                logger.warning(
                    f"Agent '{name}': Empty/trivial output detected ({consecutive_empty_outputs}/{MAX_CONSECUTIVE_EMPTY})"
                )
                if consecutive_empty_outputs >= MAX_CONSECUTIVE_EMPTY:
                    logger.error(
                        f"Agent '{name}': Exiting early after {MAX_CONSECUTIVE_EMPTY} consecutive empty outputs"
                    )
                    if verbose:
                        safe_print(f"⚠️ Model stuck - {MAX_CONSECUTIVE_EMPTY} consecutive empty outputs")
                    # Return whatever partial output we have (could be empty)
                    break
                # Continue to retry
                if attempt < max_retries - 1:
                    backoff_seconds = 2 ** attempt
                    time.sleep(backoff_seconds)
                    continue
            else:
                # Reset counter on successful non-empty output
                consecutive_empty_outputs = 0

            logger.debug(f"Agent '{name}': Generated {len(output)} chars in {time.time() - start_time:.1f}s")

            # Track token usage if tracker is provided
            if token_tracker and hasattr(response, 'usage_metadata'):
                try:
                    meta = response.usage_metadata
                    token_tracker.add_call(
                        stage=token_stage or name,
                        input_tokens=getattr(meta, 'prompt_token_count', 0) or 0,
                        output_tokens=getattr(meta, 'candidates_token_count', 0) or 0,
                    )
                except Exception:
                    pass  # Never break generation for tracking failures

            # Validate output if validators provided (and not skipped)
            if validators and not skip_validation:
                validation_passed = True
                for i, validator in enumerate(validators):
                    logger.debug(f"Agent '{name}': Running validator {i+1}/{len(validators)}")
                    result = validator(output)

                    if not result.is_valid:
                        validation_passed = False
                        logger.warning(
                            f"Agent '{name}': Validation failed on attempt {attempt+1}/{max_retries}: "
                            f"{result.error_message}"
                        )

                        if verbose:
                            safe_print(f"⚠️ Validation failed: {result.error_message}")

                        # If not last attempt, retry with backoff
                        if attempt < max_retries - 1:
                            backoff_seconds = 2 ** attempt  # Exponential: 1s, 2s, 4s
                            logger.debug(f"Agent '{name}': Backing off for {backoff_seconds}s")
                            time.sleep(backoff_seconds)
                            break  # Break validator loop to retry LLM call
                        else:
                            # Last attempt failed - raise error
                            error_msg = (
                                f"Agent '{name}' validation failed after {max_retries} attempts: "
                                f"{result.error_message}"
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                    else:
                        logger.debug(f"Agent '{name}': Validator {i+1} passed")

                # If all validators passed, break retry loop
                if validation_passed:
                    logger.info(f"Agent '{name}': All {len(validators)} validators passed")
                    break
            else:
                # No validators - success on first attempt
                logger.debug(f"Agent '{name}': No validators, accepting output")
                break

        except Exception as e:
            if verbose:
                safe_print(f"❌ Error")

            logger.error(f"Agent '{name}': Exception on attempt {attempt+1}: {str(e)}")

            # Track failed call if tracker is provided
            if token_tracker:
                try:
                    token_tracker.add_call(
                        stage=token_stage or name,
                        input_tokens=0,
                        output_tokens=0,
                        status=CallStatus.FAILURE,
                        error_message=str(e),
                    )
                except Exception:
                    pass  # Never break generation for tracking failures

            # If not last attempt and it's a transient error, retry
            if attempt < max_retries - 1 and _is_transient_error(e):
                backoff_seconds = 2 ** attempt
                logger.debug(f"Agent '{name}': Transient error, retrying after {backoff_seconds}s")
                time.sleep(backoff_seconds)
                continue
            else:
                # Partial output capture (V3 feature): on timeout, check for any files written
                if save_to and ('timeout' in str(e).lower() or 'timed out' in str(e).lower()):
                    partial_output = _capture_partial_output(save_to, name)
                    if partial_output:
                        logger.warning(
                            f"Agent '{name}': Timeout, but captured partial output ({len(partial_output)} chars)"
                        )
                        if verbose:
                            safe_print(f"⚠️ Timeout - captured {len(partial_output)} chars of partial output")
                        output = partial_output
                        break  # Exit retry loop with partial output
                raise Exception(f"Agent '{name}' execution failed: {str(e)}") from e

    elapsed = time.time() - start_time

    # Save output if path provided
    if save_to:
        try:
            save_to.parent.mkdir(parents=True, exist_ok=True)
            with open(save_to, 'w', encoding='utf-8') as f:
                f.write(output)

            # Verify file was created successfully
            if not save_to.exists():
                raise IOError(f"Output file not created: {save_to}")

            file_size = save_to.stat().st_size
            if file_size == 0:
                raise IOError(f"Output file is empty: {save_to}")

            logger.info(f"Agent '{name}': Saved output to {save_to} ({file_size} bytes)")

        except Exception as e:
            logger.error(f"Agent '{name}': Failed to save output to {save_to}: {str(e)}")
            raise

    if verbose:
        safe_print(f"✅ Done ({elapsed:.1f}s, {len(output):,} chars)")
        if save_to:
            safe_print(f"Saved to: {save_to}")

    return output


def _capture_partial_output(save_path: Path, agent_name: str) -> Optional[str]:
    """
    Capture partial output on timeout (V3 feature).

    When an agent times out, check if any partial work was written to the output
    directory. This recovers work that would otherwise be lost.

    Args:
        save_path: Path where output would be saved
        agent_name: Name of the agent (for logging)

    Returns:
        str or None: Partial output if found, None otherwise
    """
    try:
        output_dir = save_path.parent
        if not output_dir.exists():
            return None

        # Check for the output file itself (might have been partially written)
        if save_path.exists() and save_path.stat().st_size > 0:
            content = save_path.read_text(encoding='utf-8')
            if len(content.strip()) > 50:  # Non-trivial content
                logger.info(f"Agent '{agent_name}': Found partial output in {save_path}")
                return content

        # Check for any recent files in the output directory
        recent_files = []
        for f in output_dir.iterdir():
            if f.is_file() and f.suffix in ['.md', '.txt', '.json']:
                recent_files.append((f, f.stat().st_mtime))

        if not recent_files:
            return None

        # Get most recently modified file
        recent_files.sort(key=lambda x: x[1], reverse=True)
        most_recent = recent_files[0][0]

        if most_recent.stat().st_size > 0:
            content = most_recent.read_text(encoding='utf-8')
            if len(content.strip()) > 50:
                logger.info(f"Agent '{agent_name}': Found partial output in {most_recent}")
                return content

        return None
    except Exception as e:
        logger.debug(f"Agent '{agent_name}': Partial output capture failed: {e}")
        return None


def _is_transient_error(error: Exception) -> bool:
    """
    Check if error is transient and worth retrying.
    Also signals backpressure system for rate limit errors.

    Args:
        error: Exception to check

    Returns:
        bool: True if error is transient (network, rate limit, etc.)
    """
    error_str = str(error).lower()
    # Expanded patterns from V3 - covers more network/API edge cases
    transient_patterns = [
        # Rate limiting
        'timeout',
        'rate limit',
        'rate_limit',
        'ratelimit',
        'quota',
        'throttl',
        '429',  # Too Many Requests
        # Server errors
        'service unavailable',
        'temporarily unavailable',
        'server error',
        'internal error',
        '500',  # Internal Server Error
        '502',  # Bad Gateway
        '503',  # Service Unavailable
        '504',  # Gateway Timeout
        # Network errors
        'connection reset',
        'connection refused',
        'connection closed',
        'server disconnected',
        'broken pipe',
        'network unreachable',
        'dns',
        'ssl',
        'certificate',
        'handshake',
        # API-specific
        'resource exhausted',
        'overloaded',
        'capacity',
        'retry',
        'try again',
        'temporary',
    ]

    is_transient = any(pattern in error_str for pattern in transient_patterns)
    
    # Signal backpressure for rate limit errors
    if is_transient and ('429' in error_str or 'rate limit' in error_str or 'quota' in error_str):
        try:
            from utils.backpressure import BackpressureManager, APIType
            bp = BackpressureManager()
            bp.signal_429(APIType.GEMINI_PRIMARY)
            logger.debug("Signaled backpressure for rate limit error")
        except Exception:
            pass  # Don't fail on backpressure errors
    
    return is_transient


def rate_limit_delay(seconds: Optional[float] = None) -> None:
    """
    Sleep for rate limiting with tier-adaptive delays.

    Automatically adjusts delay based on detected API tier:
    - Free tier (10 RPM): 7 seconds (safe for 1 req/6s limit)
    - Paid tier (2,000 RPM): 0.3 seconds (safe for high throughput)

    Args:
        seconds: Manual override (default: None = use tier-adaptive delay)
    """
    if seconds is None:
        # Use tier-adaptive delay
        config = get_concurrency_config(verbose=False)
        seconds = config.rate_limit_delay

    time.sleep(seconds)


def research_citations_via_api(
    model: Any,
    research_topics: Optional[List[str]] = None,
    output_path: Optional[Path] = None,
    target_minimum: int = 50,
    academic_level: Optional[str] = None,
    verbose: bool = True,
    # Deep Research Mode parameters
    use_deep_research: bool = False,
    topic: Optional[str] = None,
    scope: Optional[str] = None,
    seed_references: Optional[List[str]] = None,
    min_sources_deep: int = 100,
    # Timeout control
    per_topic_timeout_seconds: int = 45,
    # Progress reporting
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """
    Research citations using API-backed fallback chain with optional deep research mode.

    Two modes of operation:
    1. **Standard Mode** (use_deep_research=False):
       - Uses manually provided research_topics list
       - Executes each topic through API fallback chain
       - Best for targeted, curated research queries

    2. **Deep Research Mode** (use_deep_research=True):
       - Uses DeepResearchPlanner for autonomous research strategy
       - Creates 50+ systematic queries from topic + scope + seed references
       - Best for comprehensive literature reviews (dissertations, draft)

    API Fallback Chain:
    Crossref → OpenAlex → OpenAIRE → CORE → DOAJ → Semantic Scholar (supplemental) → LLM fallback

    Args:
        model: Configured model instance (used for planning and optional LLM fallback)
        research_topics: List of research topics (required if use_deep_research=False)
        output_path: Path to save Scout-compatible markdown output (required if provided)
        target_minimum: Minimum citations required to pass quality gate (default: 50)
        verbose: Whether to print progress messages (default: True)

        use_deep_research: Enable deep research mode (default: False)
        topic: Main research topic (required if use_deep_research=True)
        scope: Optional research scope constraints (e.g., "EU focus; B2C and B2B")
        seed_references: Optional seed papers to expand from
        min_sources_deep: Minimum sources for deep research (default: 100)
        per_topic_timeout_seconds: Maximum time to spend on each research topic (default: 90)
        progress_callback: Optional callback(message, event_type) for progress reporting

    Returns:
        Dict with keys:
            - citations: List[Citation] - Valid citations found
            - count: int - Number of valid citations
            - sources: Dict[str, int] - Breakdown by source (Crossref, Semantic Scholar, etc.)
            - failed_topics: List[str] - Topics that failed to find citations
            - research_plan: Optional[Dict] - Deep research plan (if deep mode enabled)

    Raises:
        ValueError: If citation count < target_minimum (quality gate failure)
        ValueError: If invalid mode parameters (missing required args)
    """
    # Validate mode parameters
    if use_deep_research:
        if not topic:
            raise ValueError("Deep research mode requires 'topic' parameter")
        mode_name = "DEEP RESEARCH MODE"
    else:
        if not research_topics:
            raise ValueError("Standard mode requires 'research_topics' parameter")
        mode_name = "STANDARD MODE"

    if verbose:
        safe_print("\n" + "=" * 80)
        safe_print(f"🔬 API-BACKED SCOUT - {mode_name}")
        safe_print("=" * 80)

    # Deep Research Mode: Autonomous research planning
    research_plan: Optional[Dict[str, Any]] = None

    if use_deep_research:
        if verbose:
            safe_print(f"\n🧠 Deep Research Planning Phase")
            safe_print(f"{'='*80}")
            safe_print(f"\n📋 Input:")
            safe_print(f"   Topic: {topic}")
            if scope:
                safe_print(f"   Scope: {scope}")
            if seed_references:
                safe_print(f"   Seed References: {len(seed_references)}")
            safe_print(f"   Target: {min_sources_deep}+ sources")
            safe_print()

        # Initialize deep research planner
        planner = DeepResearchPlanner(
            llm_model=model,
            min_sources=min_sources_deep,
            verbose=verbose
        )

        # #region agent log
        import json as json_lib
        import time as time_lib
        debug_log_path = "/tmp/opendraft_debug.log"
        try:
            with open(debug_log_path, "a") as f:
                f.write(json_lib.dumps({
                    "timestamp": int(time_lib.time() * 1000),
                    "location": "agent_runner.py:research_citations_via_api",
                    "message": "Starting deep research plan creation",
                    "data": {"topic": topic[:100]},
                    "sessionId": "debug-session",
                    "runId": "run1",
                    "hypothesisId": "A"
                }) + "\n")
        except Exception:
            pass
        # #endregion

        try:
            # Create research plan
            research_plan = planner.create_research_plan(
                topic=topic,
                scope=scope,
                seed_references=seed_references
            )

            # Validate plan quality
            if not planner.validate_plan(research_plan):
                if verbose:
                    safe_print("\n⚠️  Initial plan validation failed - attempting refinement...")

                # Attempt refinement
                research_plan = planner.refine_plan(
                    plan=research_plan,
                    feedback=f"Insufficient queries or coverage. Need minimum {min_sources_deep} sources. "
                            f"Generate more diverse queries covering: author searches, title searches, "
                            f"topic queries, regulatory/standards, and interdisciplinary connections."
                )

                # Validate again
                if not planner.validate_plan(research_plan):
                    raise ValueError(
                        f"Deep research plan validation failed after refinement. "
                        f"Generated {len(research_plan.get('queries', []))} queries, "
                        f"estimated {planner.estimate_coverage(research_plan.get('queries', []))} sources, "
                        f"but need minimum {min_sources_deep}."
                    )

            # Extract queries as research topics and cap by execution capacity
            config = get_concurrency_config(verbose=False)
            raw_topics = research_plan.get('queries', [])
            research_topics = _prioritize_research_queries(
                raw_topics,
                topic=topic or "",
                academic_level=academic_level,
                parallel_workers=config.scout_parallel_workers,
            )
            research_topics = planner.optimize_queries_for_retrieval(
                topic=topic or "",
                queries=research_topics,
                scope=scope,
            )
            research_topics = _prioritize_research_queries(
                research_topics,
                topic=topic or "",
                academic_level=academic_level,
                parallel_workers=config.scout_parallel_workers,
            )

            if verbose:
                safe_print(f"\n✅ Research Plan Created:")
                safe_print(f"   Queries Generated: {len(raw_topics)}")
                safe_print(f"   Queries Selected for Execution: {len(research_topics)}")
                safe_print(f"   Estimated Coverage: {planner.estimate_coverage(research_topics)} sources")
                safe_print(f"\n📝 Research Strategy:")
                strategy_lines = research_plan.get('strategy', '').split('\n')
                for line in strategy_lines[:5]:  # First 5 lines
                    safe_print(f"   {line}")
                if len(strategy_lines) > 5:
                    safe_print(f"   ... (see output file for full strategy)")
                safe_print()
        
        except (TimeoutError, FuturesTimeoutError) as e:
            # Fallback to standard mode: generate basic queries from topic
            logger.warning(f"Deep research planning timed out, falling back to standard mode: {e}")
            if verbose:
                safe_print(f"\n⚠️  Deep Research Planning Timeout")
                safe_print(f"   Falling back to standard mode with basic queries...")
                safe_print()
            
            # #region agent log
            try:
                with open(debug_log_path, "a") as f:
                    f.write(json_lib.dumps({
                        "timestamp": int(time_lib.time() * 1000),
                        "location": "agent_runner.py:research_citations_via_api",
                        "message": "Deep research timeout - falling back to standard mode",
                        "data": {"error": str(e)[:200]},
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "A"
                    }) + "\n")
            except Exception:
                pass
            # #endregion
            
            research_topics = _prioritize_research_queries(
                _build_research_fallback_queries(topic, scope),
                topic=topic or "",
                academic_level=academic_level,
                parallel_workers=get_concurrency_config(verbose=False).scout_parallel_workers,
            )
            research_topics = planner.optimize_queries_for_retrieval(
                topic=topic or "",
                queries=research_topics,
                scope=scope,
            )
            
            if verbose:
                safe_print(f"   Generated {len(research_topics)} fallback queries")
                safe_print()
        
        except Exception as e:
            # Provider/model-level degradation path for quota/403 failures:
            # try alternate planner models before falling back to weak deterministic mode.
            recovered_with_alt_model = False
            err_lower = str(e).lower()
            quota_like = any(k in err_lower for k in ["403", "quota", "rate limit", "resource exhausted", "insufficient_quota"])
            if quota_like:
                fallback_models = [
                    m.strip()
                    for m in os.getenv(
                        "DEEP_RESEARCH_MODEL_FALLBACKS",
                        "qwen-plus,qwen-turbo,gpt-4o-mini,claude-3-5-sonnet"
                    ).split(",")
                    if m.strip()
                ]
                for fallback_model_name in fallback_models:
                    try:
                        fallback_model = create_llm_model(model_override=fallback_model_name)
                        fallback_planner = DeepResearchPlanner(
                            llm_model=fallback_model,
                            min_sources=min_sources_deep,
                            verbose=verbose,
                        )
                        research_plan = fallback_planner.create_research_plan(
                            topic=topic,
                            scope=scope,
                            seed_references=seed_references,
                        )
                        if fallback_planner.validate_plan(research_plan):
                            raw_topics = research_plan.get('queries', [])
                            research_topics = _prioritize_research_queries(
                                raw_topics,
                                topic=topic or "",
                                academic_level=academic_level,
                                parallel_workers=get_concurrency_config(verbose=False).scout_parallel_workers,
                            )
                            research_topics = fallback_planner.optimize_queries_for_retrieval(
                                topic=topic or "",
                                queries=research_topics,
                                scope=scope,
                            )
                            research_topics = _prioritize_research_queries(
                                research_topics,
                                topic=topic or "",
                                academic_level=academic_level,
                                parallel_workers=get_concurrency_config(verbose=False).scout_parallel_workers,
                            )
                            planner = fallback_planner
                            recovered_with_alt_model = True
                            if verbose:
                                safe_print(f"\n🔁 Deep planning recovered using fallback model: {fallback_model_name}")
                                safe_print(f"   Queries Selected for Execution: {len(research_topics)}")
                            break
                    except Exception as fallback_err:
                        logger.warning(f"Fallback planner model '{fallback_model_name}' failed: {fallback_err}")

            if recovered_with_alt_model:
                pass
            else:
                # For other exceptions, also fallback to standard mode
                logger.warning(f"Deep research planning failed, falling back to standard mode: {e}")
                if verbose:
                    safe_print(f"\n⚠️  Deep Research Planning Failed")
                    safe_print(f"   Error: {str(e)[:200]}")
                    safe_print(f"   Falling back to standard mode with basic queries...")
                    safe_print()
                
                # #region agent log
                try:
                    with open(debug_log_path, "a") as f:
                        f.write(json_lib.dumps({
                            "timestamp": int(time_lib.time() * 1000),
                            "location": "agent_runner.py:research_citations_via_api",
                            "message": "Deep research failed - falling back to standard mode",
                            "data": {"error": str(e)[:200]},
                            "sessionId": "debug-session",
                            "runId": "run1",
                            "hypothesisId": "A"
                        }) + "\n")
                except Exception:
                    pass
                # #endregion
                
                research_topics = _prioritize_research_queries(
                    _build_research_fallback_queries(topic, scope),
                    topic=topic or "",
                    academic_level=academic_level,
                    parallel_workers=get_concurrency_config(verbose=False).scout_parallel_workers,
                )
                research_topics = planner.optimize_queries_for_retrieval(
                    topic=topic or "",
                    queries=research_topics,
                    scope=scope,
                )
                
                if verbose:
                    safe_print(f"   Generated {len(research_topics)} fallback queries")
                    safe_print()

    # Execution Phase: Run queries through API fallback chain
    if verbose:
        safe_print(f"\n📊 Execution Configuration:")
        safe_print(f"   Target Minimum: {target_minimum} citations")
        if academic_level:
            safe_print(f"   Academic Level: {academic_level}")
        safe_print(f"   Research Topics/Queries: {len(research_topics)}")
        if output_path:
            safe_print(f"   Output: {output_path}")
        safe_print()

    # Initialize CitationResearcher with API fallback chain
    # Semantic Scholar can be disabled via env var if rate limited (403 errors)
    enable_semantic_scholar = os.environ.get('ENABLE_SEMANTIC_SCHOLAR', 'true').lower() != 'false'

    researcher = CitationResearcher(
        llm_model=model,
        enable_crossref=True,
        enable_openalex=True,
        enable_semantic_scholar=enable_semantic_scholar,
        enable_smart_routing=True,     # Enable query classification for source diversity
        enable_llm_fallback=False,     # DISABLED: LLM hallucinates citations
        verbose=verbose,
        progress_callback=progress_callback,  # Pass through for progress reporting
    )

    level_for_budget = (academic_level or "").lower().strip()
    default_total_budget = 600 if level_for_budget == "research_paper" else 1200
    total_budget_seconds = int(os.getenv("SCOUT_TOTAL_TIMEOUT_SECONDS", str(default_total_budget)))
    research_deadline = time.monotonic() + max(5, total_budget_seconds)
    researcher.set_deadline(research_deadline)

    def _research_deadline_exceeded(reserve_seconds: float = 3.0) -> bool:
        return time.monotonic() >= (research_deadline - reserve_seconds)

    def _remaining_research_seconds() -> float:
        return max(0.0, research_deadline - time.monotonic())

    capability = researcher.capability_matrix()
    is_chinese_topic = any('\u4e00' <= ch <= '\u9fff' for ch in ((topic or "") + " " + " ".join(research_topics or [])))

    if verbose:
        safe_print("🧪 Research Capability Preflight:")
        safe_print(f"   - Crossref: {'ready' if capability['crossref'].get('enabled') else 'off'}")
        safe_print(f"   - OpenAlex: {'ready' if capability['openalex'].get('enabled') else 'off'}")
        ss_state = capability['semantic_scholar']
        ss_text = "ready" if ss_state.get('enabled') and not ss_state.get('cooled_down') else "degraded"
        safe_print(f"   - OpenAIRE: {'ready' if capability['openaire'].get('enabled') else 'off'}")
        safe_print(f"   - CORE: {'ready' if capability['core'].get('enabled') else 'off'}")
        safe_print(f"   - DOAJ: {'ready' if capability['doaj'].get('enabled') else 'off'}")
        safe_print(f"   - Semantic Scholar (supplemental): {ss_text}")
        safe_print(f"   - Scout time budget: {total_budget_seconds}s")

    if not enable_semantic_scholar and verbose:
        safe_print("   ⚠️  Semantic Scholar disabled (ENABLE_SEMANTIC_SCHOLAR=false)")

    # Track results
    citations: List[Citation] = []
    sources_breakdown: Dict[str, int] = {
        "Crossref": 0,
        "OpenAlex": 0,
        "OpenAIRE": 0,
        "CORE": 0,
        "DOAJ": 0,
        "Semantic Scholar": 0,
        "LLM Fallback": 0,
    }
    failed_topics: List[str] = []
    planner_query_count = len(research_topics or [])

    # Parallel citation research configuration (tier-adaptive)
    config = get_concurrency_config(verbose=False)
    BATCH_SIZE = config.scout_batch_size
    BATCH_DELAY = config.scout_batch_delay
    PARALLEL_WORKERS = config.scout_parallel_workers
    level_for_runtime = (academic_level or "").lower().strip()
    topic_is_chinese_for_runtime = bool(re.search(r'[\u4e00-\u9fff]', topic or ""))
    if level_for_runtime == "research_paper":
        BATCH_SIZE = min(BATCH_SIZE, 3)
        PARALLEL_WORKERS = min(PARALLEL_WORKERS, 2)
    if level_for_runtime == "research_paper" and topic_is_chinese_for_runtime:
        BATCH_SIZE = min(BATCH_SIZE, 2)
        PARALLEL_WORKERS = min(PARALLEL_WORKERS, 1)

    # Detect if proxies are configured for rate limit bypass
    from utils.api_citations.base import PROXY_LIST
    use_proxies = len(PROXY_LIST) > 0

    # Adjust batch delay based on proxy availability
    # With proxies: skip delays for maximum throughput
    # Without proxies: respect API rate limits
    effective_batch_delay = 0 if use_proxies else BATCH_DELAY

    if verbose and use_proxies:
        safe_print(f"\n🔀 Proxy rotation enabled: {len(PROXY_LIST)} proxies")
        safe_print(f"   Batch delays disabled for maximum throughput")

    # Helper function for parallel execution with timeout
    def _research_single_topic(
        topic_with_idx: Tuple[int, str],
        timeout_seconds: int,
    ) -> Tuple[int, str, List[Citation], Optional[str]]:
        """Research a single topic with timeout. Returns (idx, topic, list_of_citations, error_or_None)."""
        idx, research_topic = topic_with_idx
        try:
            if _research_deadline_exceeded():
                return (idx, research_topic, [], "Global Scout time budget exhausted")
            citations_list = researcher.research_citation(research_topic)
            return (idx, research_topic, citations_list, None)
        except Exception as e:
            return (idx, research_topic, [], str(e))

    # Dynamic early stopping threshold to avoid unnecessary long runs
    early_stop_threshold = min(max(target_minimum + 3, int(target_minimum * 1.15)), 28)
    min_relevance_pass_rate_for_early_stop = 0.50
    timeout_error_count = 0
    semantic_scholar_hits = 0

    def _can_early_stop() -> bool:
        """Allow early-stop only if both count and relevance quality gates pass."""
        if len(citations) < early_stop_threshold:
            return False
        snapshot = researcher.get_metrics_snapshot()
        relevance_pass_rate = snapshot.get('relevance_pass_rate', 0.0)
        return relevance_pass_rate >= min_relevance_pass_rate_for_early_stop

    # Parallel or sequential based on config
    if PARALLEL_WORKERS > 1:
        if verbose:
            safe_print(f"\n🚀 Parallel citation research enabled ({PARALLEL_WORKERS} workers)")

        # Process in batches with parallel workers
        total_topics = len(research_topics)
        processed = 0

        # Conservative ramp-up: avoid starting at maximum worker pressure
        current_workers = min(PARALLEL_WORKERS, 2 if is_chinese_topic else 3)
        adaptive_batch_delay = float(effective_batch_delay)
        for batch_start in range(0, total_topics, BATCH_SIZE):
            if _research_deadline_exceeded():
                if verbose:
                    safe_print(f"\n⏱️  Scout time budget reached ({total_budget_seconds}s). Stopping new research queries.")
                break
            # Early stopping: requires both count and relevance quality thresholds.
            if _can_early_stop():
                if verbose:
                    snapshot = researcher.get_metrics_snapshot()
                    safe_print(
                        f"\n⏩ Early stopping: {len(citations)} citations collected "
                        f"(target: {target_minimum}, threshold: {early_stop_threshold}, "
                        f"relevance_pass_rate={snapshot.get('relevance_pass_rate', 0.0) * 100:.1f}%)"
                    )
                break

            batch_end = min(batch_start + BATCH_SIZE, total_topics)
            batch = list(enumerate(research_topics[batch_start:batch_end], batch_start + 1))

            if verbose and batch_start > 0 and adaptive_batch_delay > 0:
                safe_print(f"\n⏸️  Batch complete ({batch_start} topics processed). Waiting {adaptive_batch_delay:.1f}s to respect API limits...")
                time.sleep(adaptive_batch_delay)

            if verbose:
                safe_print(f"\n📦 Processing batch {batch_start // BATCH_SIZE + 1} ({len(batch)} topics)...")

            # Execute batch in parallel (adaptive workers under timeout pressure)
            if verbose and current_workers != PARALLEL_WORKERS:
                safe_print(f"   🔧 Adaptive workers: {current_workers} (base={PARALLEL_WORKERS})")
            batch_timeout_count = 0
            executor = ThreadPoolExecutor(max_workers=current_workers)
            futures = {}
            try:
                for item in batch:
                    if _research_deadline_exceeded():
                        break
                    idx, q = item
                    dynamic_timeout = max(20, min(45, per_topic_timeout_seconds + (5 if is_chinese_topic else 0)))
                    futures[executor.submit(_research_single_topic, (idx, q), dynamic_timeout)] = item

                batch_timeout = max(0.25, min(
                    max(1, per_topic_timeout_seconds),
                    _remaining_research_seconds() - 1.0,
                ))
                completed_futures = []
                try:
                    for future in as_completed(futures, timeout=batch_timeout):
                        completed_futures.append(future)
                except (TimeoutError, FuturesTimeoutError):
                    batch_timeout_count += sum(1 for future in futures if not future.done())
                    logger.warning(
                        f"Scout batch hard timeout after {batch_timeout:.1f}s; "
                        f"using {len(completed_futures)}/{len(futures)} completed topic results"
                    )
                    for future in futures:
                        if not future.done():
                            future.cancel()

                    # Some topic workers can complete between as_completed's
                    # timeout and cancellation. Preserve those results instead
                    # of dropping the whole topic batch under timeout pressure.
                    for future in futures:
                        if future.done() and not future.cancelled() and future not in completed_futures:
                            completed_futures.append(future)

                for future in completed_futures:
                    idx, research_topic, citations_list, error = future.result(timeout=0)
                    processed += 1

                    if verbose:
                        safe_print(f"[{idx}/{total_topics}] 🔎 {research_topic[:55]}{'...' if len(research_topic) > 55 else ''}", end=' ')

                    if error:
                        failed_topics.append(research_topic)
                        if "timeout" in error.lower():
                            timeout_error_count += 1
                            batch_timeout_count += 1
                        if verbose:
                            safe_print(f"❌ Error: {error[:30]}...")
                        logger.error(f"Citation research failed for '{research_topic}': {error}")
                    elif citations_list:
                        # Add ALL citations from this query (multiple sources)
                        citations.extend(citations_list)
                        citations = _dedupe_citations(citations)
                        # Update source breakdown for all citations
                        for citation in citations_list:
                            source = citation.api_source or 'Unknown'
                            if source in sources_breakdown:
                                sources_breakdown[source] += 1
                            if source == "Semantic Scholar":
                                semantic_scholar_hits += 1
                        if verbose:
                            # Show all sources found for this query
                            sources_str = ", ".join([c.api_source or 'Unknown' for c in citations_list])
                            first_citation = citations_list[0]
                            authors_str = first_citation.authors[0] if first_citation.authors else "Unknown"
                            count_str = f" (+{len(citations_list)-1} more)" if len(citations_list) > 1 else ""
                            safe_print(f"✅ {authors_str} et al. ({first_citation.year}) [{sources_str}]{count_str}")

                        # Check for early stopping within batch (count + relevance)
                        if _can_early_stop():
                            if verbose:
                                snapshot = researcher.get_metrics_snapshot()
                                safe_print(
                                    f"\n⏩ Early stopping: {len(citations)} citations collected "
                                    f"(relevance_pass_rate={snapshot.get('relevance_pass_rate', 0.0) * 100:.1f}%)"
                                )
                            break
                    else:
                        failed_topics.append(research_topic)
                        if verbose:
                            safe_print("❌ No citation found")
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            # Adaptive backoff for worker count when timeout pressure is high
            if len(batch) > 0 and batch_timeout_count / len(batch) >= 0.4 and current_workers > 1:
                current_workers = max(1, current_workers // 2)
                adaptive_batch_delay = min(20.0, adaptive_batch_delay + 3.0)
                logger.warning(
                    f"High timeout pressure in batch ({batch_timeout_count}/{len(batch)}). "
                    f"Reducing workers to {current_workers}, delay to {adaptive_batch_delay:.1f}s."
                )
                if batch_timeout_count >= len(batch):
                    logger.warning("Entire batch timed out; stopping further batches to preserve global deadline")
                    break
            elif batch_timeout_count == 0 and current_workers < PARALLEL_WORKERS:
                current_workers = min(PARALLEL_WORKERS, current_workers + 1)
                adaptive_batch_delay = max(0.0, adaptive_batch_delay - 1.0)
    else:
        # Sequential execution (free tier or 1 worker)
        if verbose:
            safe_print("\n🔄 Sequential citation research (1 worker)")

        for idx, research_topic in enumerate(research_topics, 1):
            if _research_deadline_exceeded():
                if verbose:
                    safe_print(f"\n⏱️  Scout time budget reached ({total_budget_seconds}s). Stopping new research queries.")
                break
            # Early stopping: requires both count and relevance quality thresholds.
            if _can_early_stop():
                if verbose:
                    snapshot = researcher.get_metrics_snapshot()
                    safe_print(
                        f"\n⏩ Early stopping: {len(citations)} citations collected "
                        f"(target: {target_minimum}, threshold: {early_stop_threshold}, "
                        f"relevance_pass_rate={snapshot.get('relevance_pass_rate', 0.0) * 100:.1f}%)"
                    )
                break

            # Add delay every BATCH_SIZE topics to prevent burst rate limits
            if idx > 1 and (idx - 1) % BATCH_SIZE == 0 and effective_batch_delay > 0:
                if verbose:
                    safe_print(f"\n⏸️  Batch complete ({idx-1} topics processed). Waiting {effective_batch_delay}s to respect API limits...")
                time.sleep(effective_batch_delay)

            if verbose:
                safe_print(f"[{idx}/{len(research_topics)}] 🔎 {research_topic[:65]}{'...' if len(research_topic) > 65 else ''}")

            try:
                citations_list = researcher.research_citation(research_topic)

                if citations_list:
                    # #region agent log
                    # Note: json, time, os already imported at module level
                    try:
                        debug_log_path = os.getenv('DEBUG_LOG_PATH', '/tmp/opendraft/debug.log')
                        os.makedirs(os.path.dirname(debug_log_path), exist_ok=True)
                        with open(debug_log_path, 'a') as f:
                            f.write(json.dumps({
                                "sessionId": "debug-session",
                                "runId": "run1",
                                "hypothesisId": "J",
                                "location": "agent_runner.py:710",
                                "message": "Citations found for topic",
                                "data": {
                                    "topic": research_topic[:100],
                                    "citations_count": len(citations_list),
                                    "sources": [c.api_source or 'Unknown' for c in citations_list],
                                    "total_citations_so_far": len(citations) + len(citations_list)
                                },
                                "timestamp": int(time.time() * 1000)
                            }) + "\n")
                    except Exception as e:
                        logger.debug(f"Debug log write failed: {e}")
                    # #endregion
                    
                    # Add ALL citations from this query (multiple sources)
                    citations.extend(citations_list)
                    citations = _dedupe_citations(citations)

                    # Track sources for all citations
                    for citation in citations_list:
                        source = citation.api_source or 'Unknown'
                        if source in sources_breakdown:
                            sources_breakdown[source] += 1

                    if verbose:
                        # Show all sources found for this query
                        sources_str = ", ".join([c.api_source or 'Unknown' for c in citations_list])
                        first_citation = citations_list[0]
                        authors_str = first_citation.authors[0] if first_citation.authors else "Unknown"
                        count_str = f" (+{len(citations_list)-1} more)" if len(citations_list) > 1 else ""
                        safe_print(f"    ✅ {authors_str} et al. ({first_citation.year}) [{sources_str}]{count_str}")
                else:
                    failed_topics.append(research_topic)
                    if verbose:
                        safe_print(f"    ❌ No citation found")

            except Exception as e:
                failed_topics.append(research_topic)
                if verbose:
                    safe_print(f"    ❌ Error: {str(e)}")
                logger.error(f"Citation research failed for '{research_topic}': {str(e)}")

    # Calculate success metrics
    citations = _dedupe_citations(citations)
    citation_count = len(citations)
    success_rate = (citation_count / len(research_topics) * 100) if research_topics else 0
    research_metrics = researcher.get_metrics_snapshot()
    timeout_rate = (timeout_error_count / max(1, len(research_topics))) * 100
    research_metrics["timeout_rate"] = timeout_rate
    research_metrics["success_rate"] = success_rate
    research_metrics["citation_count"] = citation_count
    raw_candidates = int(research_metrics.get("candidates_seen_raw", 0) or 0)
    normalized_candidates = int(research_metrics.get("normalized_candidates", 0) or 0)
    accepted_candidates = int(research_metrics.get("candidates_accepted", 0) or 0)

    # Zero-hit retrievability recovery: treat "no candidates" differently from low relevance.
    if not research_metrics.get("retrievability_ready", False) and not _research_deadline_exceeded(30.0):
        retrievability_queries = _prioritize_research_queries(
            _build_quality_rescue_queries(topic or "", scope=scope, is_chinese_topic=is_chinese_topic)
            + _build_research_fallback_queries(topic or "", scope=scope),
            topic=topic or "",
            academic_level=academic_level,
            parallel_workers=PARALLEL_WORKERS,
        )
        retrievability_candidates = [q for q in retrievability_queries if q not in (research_topics or [])][:2]
        if retrievability_candidates and verbose:
            safe_print("⚠️  Zero-candidate retrieval detected. Running retrievability recovery queries...")
        for recovery_query in retrievability_candidates:
            if _research_deadline_exceeded(15.0):
                break
            try:
                extra = researcher.research_citation(recovery_query)
                if extra:
                    citations.extend(extra)
                    citations = _dedupe_citations(citations)
                    for citation in extra:
                        source = citation.api_source or 'Unknown'
                        if source in sources_breakdown:
                            sources_breakdown[source] += 1
            except Exception as recovery_err:
                logger.warning(f"Retrievability recovery query failed '{recovery_query}': {recovery_err}")

        citation_count = len(citations)
        research_metrics = researcher.get_metrics_snapshot()
        research_metrics["timeout_rate"] = timeout_rate
        research_metrics["success_rate"] = success_rate
        research_metrics["citation_count"] = citation_count

    scout_mode = (os.getenv("SCOUT_MODE", "quality") or "quality").lower().strip()
    quality_mode = scout_mode == "quality"

    # Chinese-topic guardrail: signal missing Chinese-language coverage loudly
    chinese_language_hits = _count_chinese_language_hits(citations)
    title_zh_hits = chinese_language_hits["title_zh_hits"]
    language_zh_hits = chinese_language_hits["language_zh_hits"]
    effective_zh_hits = chinese_language_hits["effective_zh_hits"]

    seed_count = 0
    expanded_count = 0
    if quality_mode and not _research_deadline_exceeded(20.0):
        seeds = _select_seed_papers(citations, max_seed_papers=5)
        seed_count = len(seeds)
        if seed_count >= 3:
            expanded = _expand_from_seed_papers(
                researcher=researcher,
                seeds=seeds,
                topic=topic or " ".join(research_topics or []),
                budget_seconds=60,
                max_total_expanded=50,
            )
            expanded_count = len(expanded)
            if expanded:
                citations.extend(expanded)
                citations = _dedupe_citations(citations)
                citation_count = len(citations)

    if verbose and timeout_error_count > 0:
        safe_print(f"⚠️  Timeout diagnostics: {timeout_error_count} topic timeouts observed")
    if verbose and semantic_scholar_hits == 0 and enable_semantic_scholar:
        safe_print("⚠️  Semantic Scholar yielded 0 accepted hits in this run (likely rate-limit/cooldown pressure)")

    # Rate-limit compensation: run only when count/relevance is genuinely weak.
    # Semantic Scholar is supplemental; zero S2 hits alone should not trigger a
    # compensation storm.
    if (
        not _research_deadline_exceeded(30.0)
        and (
            citation_count < max(1, target_minimum // 2)
            or research_metrics.get('relevance_pass_rate', 0.0) < min_relevance_pass_rate_for_early_stop
        )
    ):
        compensation_seed_queries = _prioritize_research_queries(
            _build_research_fallback_queries(topic or "", scope),
            topic=topic or "",
            academic_level=academic_level,
            parallel_workers=PARALLEL_WORKERS,
        )
        if use_deep_research and topic and level_for_budget != "research_paper":
            planner = planner if 'planner' in locals() else DeepResearchPlanner(
                llm_model=model,
                min_sources=min_sources_deep,
                verbose=verbose,
            )
            compensation_seed_queries = planner.optimize_queries_for_retrieval(
                topic=topic or "",
                queries=compensation_seed_queries,
                scope=scope,
            )
            compensation_seed_queries = _prioritize_research_queries(
                compensation_seed_queries,
                topic=topic or "",
                academic_level=academic_level,
                parallel_workers=PARALLEL_WORKERS,
            )
        compensation_queries = [
            q for q in compensation_seed_queries
            if q not in (research_topics or [])
        ][:1]
        if compensation_queries and verbose:
            safe_print("⚠️  Running limited compensation query for quality recovery...")

        for extra_query in compensation_queries:
            if _research_deadline_exceeded(15.0):
                break
            try:
                extra_citations = researcher.research_citation(extra_query)
                if extra_citations:
                    citations.extend(extra_citations)
                    citations = _dedupe_citations(citations)
                    for citation in extra_citations:
                        source = citation.api_source or 'Unknown'
                        if source in sources_breakdown:
                            sources_breakdown[source] += 1
                        if source == "Semantic Scholar":
                            semantic_scholar_hits += 1
            except Exception as e:
                logger.warning(f"Compensation query failed '{extra_query}': {e}")

        citation_count = len(citations)
        research_metrics = researcher.get_metrics_snapshot()

    if verbose:
        safe_print("\n" + "=" * 80)
        safe_print("📊 SCOUT RESULTS")
        safe_print("=" * 80)
        safe_print(f"\n✅ Valid Citations: {citation_count}")
        safe_print(f"❌ Failed Topics: {len(failed_topics)}")
        safe_print(f"📈 Success Rate: {success_rate:.1f}%")
        safe_print(f"🧪 Accepted Rate: {research_metrics.get('accepted_rate', 0.0) * 100:.1f}%")
        safe_print(f"🎯 Relevance Pass Rate: {research_metrics.get('relevance_pass_rate', 0.0) * 100:.1f}%")
        safe_print(f"⏱️ Timeout Rate: {timeout_rate:.1f}%")
        safe_print(f"\n📚 Sources Breakdown:")
        for source, count in sources_breakdown.items():
            percentage = (count / citation_count * 100) if citation_count > 0 else 0
            safe_print(f"   {source}: {count} ({percentage:.1f}%)")
        if is_chinese_topic:
            safe_print("\n🇨🇳 Chinese Language Coverage:")
            safe_print(f"   Chinese title hits: {title_zh_hits}")
            safe_print(f"   Chinese language-tag hits: {language_zh_hits}")
            safe_print(f"   Effective Chinese hits: {effective_zh_hits}")
        safe_print()

    # Tiered Quality Gate
    excellent_threshold = target_minimum
    acceptable_threshold = int(target_minimum * 0.9)
    minimal_threshold = int(target_minimum * 0.85)
    level = (academic_level or "").lower().strip()

    # Additional quality gates for relevance/preprint/recency in research paper mode.
    min_relevance_quality_gate = 0.45 if level == "research_paper" else 0.35
    max_preprint_ratio = 0.30 if level == "research_paper" else 0.40
    min_recent_ratio = 0.50 if level == "research_paper" else 0.35

    current_year = time.localtime().tm_year
    recent_citations = [c for c in citations if getattr(c, 'year', 0) and int(getattr(c, 'year', 0)) >= current_year - 8]
    recent_ratio = (len(recent_citations) / citation_count) if citation_count > 0 else 0.0
    preprint_count = sum(1 for c in citations if _is_preprint_citation(c))
    preprint_ratio = (preprint_count / citation_count) if citation_count > 0 else 0.0

    quality_rescue_needed = (
        research_metrics.get('relevance_pass_rate', 0.0) < min_relevance_quality_gate
        or preprint_ratio > max_preprint_ratio
        or recent_ratio < min_recent_ratio
    )
    if quality_rescue_needed and not _research_deadline_exceeded(30.0):
        quality_rescue_seed_queries = _prioritize_research_queries(
            _build_quality_rescue_queries(
                topic or "",
                scope=scope,
                is_chinese_topic=is_chinese_topic,
            ),
            topic=topic or "",
            academic_level=academic_level,
            parallel_workers=PARALLEL_WORKERS,
        )
        if use_deep_research and topic and level != "research_paper":
            planner = planner if 'planner' in locals() else DeepResearchPlanner(
                llm_model=model,
                min_sources=min_sources_deep,
                verbose=verbose,
            )
            quality_rescue_seed_queries = planner.optimize_queries_for_retrieval(
                topic=topic or "",
                queries=quality_rescue_seed_queries,
                scope=scope,
            )
            quality_rescue_seed_queries = _prioritize_research_queries(
                quality_rescue_seed_queries,
                topic=topic or "",
                academic_level=academic_level,
                parallel_workers=PARALLEL_WORKERS,
            )
        quality_rescue_queries = [
            q for q in quality_rescue_seed_queries
            if q.strip() and q not in (research_topics or [])
        ][:2]
        if quality_rescue_queries and verbose:
            safe_print("⚠️  Running focused academic rescue queries for relevance/quality gate...")

        for rescue_query in quality_rescue_queries:
            if _research_deadline_exceeded(15.0):
                break
            try:
                rescue_citations = researcher.research_citation(rescue_query)
                if rescue_citations:
                    citations.extend(rescue_citations)
                    citations = _dedupe_citations(citations)
                    for citation in rescue_citations:
                        source = citation.api_source or 'Unknown'
                        if source in sources_breakdown:
                            sources_breakdown[source] += 1
            except Exception as e:
                logger.warning(f"Quality rescue query failed '{rescue_query}': {e}")

        citation_count = len(citations)
        research_metrics = researcher.get_metrics_snapshot()
        recent_citations = [c for c in citations if getattr(c, 'year', 0) and int(getattr(c, 'year', 0)) >= current_year - 8]
        recent_ratio = (len(recent_citations) / citation_count) if citation_count > 0 else 0.0
        preprint_count = sum(1 for c in citations if _is_preprint_citation(c))
        preprint_ratio = (preprint_count / citation_count) if citation_count > 0 else 0.0

    failure_type = _classify_research_quality_failure(
        raw_candidates_count=raw_candidates,
        normalized_candidates_count=normalized_candidates,
        accepted_candidates_count=accepted_candidates,
        valid_citations_count=citation_count,
        relevance_pass_rate=float(research_metrics.get('relevance_pass_rate', 0.0) or 0.0),
        required_threshold=min_relevance_quality_gate,
        target_minimum=target_minimum,
        preprint_ratio=preprint_ratio,
        max_preprint_ratio=max_preprint_ratio,
    )

    if failure_type == "retrievability_failure":
        diagnostics = _build_research_diagnostics(
            topic=topic or "",
            scope=scope,
            planned_queries=research_topics or [],
            research_metrics=research_metrics,
            sources_breakdown=sources_breakdown,
            citation_count=citation_count,
            final_count=citation_count,
            failure_type=failure_type,
            warnings=[],
        )
        raise ValueError(
            "Research retrievability gate failed: zero valid candidates were observed across providers after recovery. "
            "This indicates retrieval unavailability/query-provider mismatch, not a relevance-quality failure. "
            f"Diagnostics: {json.dumps(diagnostics, ensure_ascii=False)}"
        )

    soft_floor = max(1, int(target_minimum * (0.50 if level == "research_paper" else 0.70)))
    allow_research_paper_degraded = level == "research_paper" and citation_count >= soft_floor

    if failure_type == "relevance_quality_failure" and not allow_research_paper_degraded:
        diagnostics = _build_research_diagnostics(
            topic=topic or "",
            scope=scope,
            planned_queries=research_topics or [],
            research_metrics=research_metrics,
            sources_breakdown=sources_breakdown,
            citation_count=citation_count,
            final_count=citation_count,
            failure_type=failure_type,
            warnings=[],
        )
        raise ValueError(
            f"Research quality gate failed: relevance_pass_rate={research_metrics.get('relevance_pass_rate', 0.0) * 100:.1f}% "
            f"< required {min_relevance_quality_gate * 100:.1f}%. Diagnostics: {json.dumps(diagnostics, ensure_ascii=False)}"
        )

    if preprint_ratio > max_preprint_ratio and not allow_research_paper_degraded:
        raise ValueError(
            f"Research quality gate failed: preprint_ratio={preprint_ratio * 100:.1f}% "
            f"> max allowed {max_preprint_ratio * 100:.1f}%"
        )

    if recent_ratio < min_recent_ratio and not allow_research_paper_degraded:
        raise ValueError(
            f"Research quality gate failed: recent_ratio={recent_ratio * 100:.1f}% "
            f"< required {min_recent_ratio * 100:.1f}% (last 8 years)"
        )
    # Chinese-topic hard floor: prevent under-supported draft generation.
    # Before failing hard, run a small zh-rescue round when primary retrieval is
    # successful overall but Chinese-language hits are still insufficient.
    # Chinese-topic hard floor: prevent under-supported draft generation
    if is_chinese_topic:
        chinese_min_required = max(1, min(3, target_minimum // 4))
        if effective_zh_hits < chinese_min_required and not _research_deadline_exceeded(30.0):
            rescue_queries = _build_chinese_coverage_rescue_queries(topic or "", scope)
            if use_deep_research and topic and level != "research_paper":
                planner = planner if 'planner' in locals() else DeepResearchPlanner(
                    llm_model=model,
                    min_sources=min_sources_deep,
                    verbose=verbose,
                )
                rescue_queries = planner.optimize_queries_for_retrieval(
                    topic=topic or "",
                    queries=rescue_queries,
                    scope=scope,
                )
                rescue_queries = _prioritize_research_queries(
                    rescue_queries,
                    topic=topic or "",
                    academic_level=academic_level,
                    parallel_workers=PARALLEL_WORKERS,
                )
            rescue_candidates = [q for q in rescue_queries if q not in (research_topics or [])]
            if rescue_candidates:
                if verbose:
                    safe_print("⚠️  Chinese coverage below threshold. Running targeted Chinese rescue queries...")
                for rescue_query in rescue_candidates[:2]:
                    if _research_deadline_exceeded(15.0):
                        break
                    try:
                        rescue_citations = researcher.research_citation(rescue_query)
                        if rescue_citations:
                            citations.extend(rescue_citations)
                            for citation in rescue_citations:
                                source = citation.api_source or 'Unknown'
                                if source in sources_breakdown:
                                    sources_breakdown[source] += 1
                    except Exception as e:
                        logger.warning(f"Chinese coverage rescue query failed '{rescue_query}': {e}")

                citation_count = len(citations)
                chinese_language_hits = _count_chinese_language_hits(citations)
                title_zh_hits = chinese_language_hits["title_zh_hits"]
                language_zh_hits = chinese_language_hits["language_zh_hits"]
                effective_zh_hits = chinese_language_hits["effective_zh_hits"]

        if effective_zh_hits < chinese_min_required and not allow_research_paper_degraded:
            raise ValueError(
                f"Chinese topic quality gate failed: Chinese-language hits {effective_zh_hits} < required {chinese_min_required} "
                f"(title_zh_hits={title_zh_hits}, language_zh_hits={language_zh_hits}). "
                "Please broaden Chinese queries (including bilingual expansions) or narrow topic scope."
            )

    # Adaptive count rescue: if near minimal threshold, run focused high-signal rescue queries
    if citation_count < minimal_threshold and citation_count >= max(1, minimal_threshold - 3) and not _research_deadline_exceeded(30.0):
        rescue_queries = (
            _build_chinese_coverage_rescue_queries(topic or "", scope)
            if is_chinese_topic
            else _build_quality_rescue_queries(topic or "", scope=scope, is_chinese_topic=False)
        )
        if use_deep_research and topic and level != "research_paper":
            planner = planner if 'planner' in locals() else DeepResearchPlanner(
                llm_model=model,
                min_sources=min_sources_deep,
                verbose=verbose,
            )
            rescue_queries = planner.optimize_queries_for_retrieval(
                topic=topic or "",
                queries=rescue_queries,
                scope=scope,
            )
            rescue_queries = _prioritize_research_queries(
                rescue_queries,
                topic=topic or "",
                academic_level=academic_level,
                parallel_workers=PARALLEL_WORKERS,
            )
        rescue_candidates = [q for q in rescue_queries if q not in (research_topics or [])]
        if rescue_candidates and verbose:
            safe_print(f"⚠️  Near quality threshold ({citation_count}/{minimal_threshold}). Running focused rescue queries...")

        for rescue_query in rescue_candidates[:2]:
            if citation_count >= minimal_threshold:
                break
            if _research_deadline_exceeded(15.0):
                break
            try:
                rescue_citations = researcher.research_citation(rescue_query)
                if rescue_citations:
                    citations.extend(rescue_citations)
                    citations = _dedupe_citations(citations)
                    citation_count = len(citations)
                    for citation in rescue_citations:
                        source = citation.api_source or 'Unknown'
                        if source in sources_breakdown:
                            sources_breakdown[source] += 1
            except Exception as e:
                logger.warning(f"Count rescue query failed '{rescue_query}': {e}")

    # Research-paper source top-up: broad social-science topics often return a
    # few solid citations but miss the minimal threshold because live APIs time
    # out or first-hit metadata is noisy. Run concise academic queries until the
    # minimal threshold is met, bounded by the global Scout budget.
    if level == "research_paper" and citation_count < minimal_threshold and not _research_deadline_exceeded(20.0):
        topup_queries = [
            q for q in _build_research_paper_topup_queries(topic or " ".join(research_topics or []), scope)
            if q not in (research_topics or [])
        ]
        if topup_queries and verbose:
            safe_print(f"⚠️  Research-paper citation count below threshold ({citation_count}/{minimal_threshold}). Running focused source top-up...")
        for topup_query in topup_queries[:8]:
            if citation_count >= minimal_threshold or _research_deadline_exceeded(10.0):
                break
            try:
                topup_citations = researcher.research_citation(topup_query)
                if topup_citations:
                    citations.extend(topup_citations)
                    citations = _dedupe_citations(citations)
                    citation_count = len(citations)
                    for citation in topup_citations:
                        source = citation.api_source or 'Unknown'
                        if source in sources_breakdown:
                            sources_breakdown[source] += 1
            except Exception as e:
                logger.warning(f"Research-paper top-up query failed '{topup_query}': {e}")

    citations = _dedupe_citations(citations)
    citation_count = len(citations)
    unique_sources_breakdown = _compute_unique_source_breakdown(citations)
    raw_candidates = int(research_metrics.get("candidates_seen_raw", 0) or 0)
    normalized_candidates = int(research_metrics.get("normalized_candidates", 0) or 0)
    accepted_candidates = int(research_metrics.get("candidates_accepted", 0) or 0)
    unique_valid_citations = citation_count
    rejected_reason_top5 = sorted(
        (research_metrics.get("rejected_reasons") or {}).items(),
        key=lambda item: item[1],
        reverse=True,
    )[:5]
    provider_error_summary = {
        "provider_errors": research_metrics.get("provider_errors", {}) or {},
        "failed_topics": len(failed_topics),
        "timeout_topics": timeout_error_count,
    }

    # #region agent log
    # Note: json, time, os already imported at module level
    try:
        debug_log_path = os.getenv('DEBUG_LOG_PATH', '/tmp/opendraft/debug.log')
        with open(debug_log_path, 'a') as f:
            f.write(json.dumps({
                "sessionId": "debug-session",
                "runId": "run1",
                "hypothesisId": "G",
                "location": "agent_runner.py:760",
                "message": "Quality gate evaluation",
                "data": {
                    "citation_count": citation_count,
                    "target_minimum": target_minimum,
                    "excellent_threshold": excellent_threshold,
                    "acceptable_threshold": acceptable_threshold,
                    "minimal_threshold": minimal_threshold,
                    "sources_breakdown": unique_sources_breakdown,
                    "title_zh_hits": title_zh_hits,
                    "language_zh_hits": language_zh_hits,
                    "effective_zh_hits": effective_zh_hits,
                    "failed_topics_count": len(failed_topics),
                    "research_metrics": research_metrics,
                    "scout_mode": scout_mode,
                    "planner_query_count": planner_query_count,
                    "executed_query_count": int(research_metrics.get("queries_executed", 0) or 0),
                    "raw_candidates": raw_candidates,
                    "accepted_candidates": accepted_candidates,
                    "seed_count": seed_count,
                    "expanded_count": expanded_count,
                },
                "timestamp": int(time.time() * 1000)
            }) + "\n")
    except Exception as e:
        logger.debug(f"Debug log write failed: {e}")
    # #endregion

    if citation_count >= excellent_threshold:
        if verbose:
            safe_print(f"✅ QUALITY GATE PASSED (EXCELLENT): {citation_count} ≥ {target_minimum} required\n")
        logger.info(f"Quality gate: EXCELLENT - {citation_count}/{target_minimum} citations")

    elif citation_count >= acceptable_threshold:
        percentage = (citation_count / target_minimum) * 100
        if verbose:
            safe_print(f"⚠️  QUALITY GATE PASSED (ACCEPTABLE): {citation_count}/{target_minimum} ({percentage:.1f}%)")
            safe_print(f"    Academic quality is good, but {target_minimum - citation_count} more citations recommended.\n")
        logger.warning(f"Quality gate: ACCEPTABLE - {citation_count}/{target_minimum} ({percentage:.1f}%)")

    elif citation_count >= minimal_threshold:
        percentage = (citation_count / target_minimum) * 100
        if verbose:
            safe_print(f"⚠️  QUALITY GATE PASSED (MINIMAL): {citation_count}/{target_minimum} ({percentage:.1f}%)")
            safe_print(f"    ⚠️  WARNING: Citation count is below recommended standards.")
            safe_print(f"    Consider adding {target_minimum - citation_count} more citations for better academic rigor.\n")
        logger.warning(f"Quality gate: MINIMAL - {citation_count}/{target_minimum} ({percentage:.1f}%) - below standards")

    elif allow_research_paper_degraded:
        percentage = (citation_count / target_minimum) * 100
        if verbose:
            safe_print(f"⚠️  QUALITY GATE PASSED (DEGRADED RESEARCH_PAPER): {citation_count}/{target_minimum} ({percentage:.1f}%)")
            safe_print("    Proceeding with available citations to avoid long-running Scout failure.")
            safe_print("    Consider rerunning with a broader topic or larger SCOUT_TOTAL_TIMEOUT_SECONDS for more sources.\n")
        logger.warning(
            f"Quality gate: DEGRADED research_paper - {citation_count}/{target_minimum} "
            f"({percentage:.1f}%) below minimal threshold {minimal_threshold}, proceeding"
        )

    else:
        percentage = (citation_count / target_minimum) * 100
        error_msg = (
            f"\n❌ QUALITY GATE FAILED (INSUFFICIENT CITATIONS)\n\n"
            f"Only {citation_count} citations found ({percentage:.1f}%), but minimum {minimal_threshold} required ({minimal_threshold/target_minimum*100:.0f}% of target).\n"
            f"Target: {target_minimum} citations (100%)\n"
            f"Acceptable: {acceptable_threshold}+ citations (90%)\n"
            f"Minimal: {minimal_threshold}+ citations (85%)\n"
            f"Current: {citation_count} citations ({percentage:.1f}%) ❌\n\n"
            f"Academic draft standards require at least {minimal_threshold} citations.\n\n"
            f"planner_query_count: {planner_query_count}\n"
            f"executed_query_count: {int(research_metrics.get('queries_executed', 0) or 0)}\n"
            f"provider_error_summary: {provider_error_summary}\n"
            f"raw_candidates: {raw_candidates}\n"
            f"accepted_candidates: {accepted_candidates}\n"
            f"relevance_pass_rate: {research_metrics.get('relevance_pass_rate', 0.0) * 100:.1f}%\n"
            f"rejected_reason_top5: {rejected_reason_top5}\n"
            f"final_valid_citation_count: {citation_count}\n\n"
            f"Failed Topics ({len(failed_topics)}):\n"
        )
        for failed_topic in failed_topics[:10]:
            error_msg += f"  - {failed_topic}\n"
        if len(failed_topics) > 10:
            error_msg += f"  ... and {len(failed_topics) - 10} more\n"

        logger.error(f"Quality gate FAILED: {citation_count} < {minimal_threshold} (minimal threshold)")
        raise ValueError(error_msg)

    # Format output as Scout-compatible markdown
    markdown_lines = [
        "# Scout Output - Academic Citation Discovery",
        "",
        "## Summary",
        "",
        f"**Total Valid Citations**: {citation_count}",
        f"**Success Rate**: {success_rate:.1f}%",
        f"**Accepted Rate**: {research_metrics.get('accepted_rate', 0.0) * 100:.1f}%",
        f"**Relevance Pass Rate**: {research_metrics.get('relevance_pass_rate', 0.0) * 100:.1f}%",
        f"**Timeout Rate**: {timeout_rate:.1f}%",
        f"**Failed Topics**: {len(failed_topics)}",
        "",
        "### Sources Breakdown",
        ""
    ]

    for source, count in unique_sources_breakdown.items():
        percentage = (count / citation_count * 100) if citation_count > 0 else 0
        markdown_lines.append(f"- **{source}**: {count} ({percentage:.1f}%)")

    markdown_lines.extend([
        "",
        "---",
        "",
        "## Citations Found",
        ""
    ])

    # Add citations grouped by source
    for source in ["Crossref", "OpenAlex", "OpenAIRE", "CORE", "DOAJ", "Semantic Scholar", "LLM Fallback"]:
        source_citations = [c for c in citations if c.api_source == source]
        if not source_citations:
            continue

        markdown_lines.append(f"### From {source} ({len(source_citations)} citations)")
        markdown_lines.append("")

        for idx, citation in enumerate(source_citations, 1):
            markdown_lines.append(f"#### {idx}. {citation.title}")
            markdown_lines.append(f"**Authors**: {', '.join(citation.authors)}")
            markdown_lines.append(f"**Year**: {citation.year}")
            markdown_lines.append(f"**DOI**: {citation.doi}")
            if citation.url:
                markdown_lines.append(f"**URL**: {citation.url}")
            if hasattr(citation, 'abstract') and citation.abstract:
                # Include abstract for Scribe to summarize (prevent hallucination)
                markdown_lines.append("")
                markdown_lines.append(f"**Abstract**: {citation.abstract}")
            markdown_lines.append("")

    if failed_topics:
        markdown_lines.extend([
            "---",
            "",
            "## Failed Topics",
            "",
            "The following topics did not return valid citations:",
            ""
        ])
        for failed_topic in failed_topics:
            markdown_lines.append(f"- {failed_topic}")

    # Write to file
    markdown_content = "\n".join(markdown_lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_content, encoding='utf-8')

    if verbose:
        safe_print(f"💾 Saved Scout output to: {output_path}")
        safe_print(f"   File size: {output_path.stat().st_size:,} bytes\n")

    logger.info(f"Scout completed: {citation_count} citations, {success_rate:.1f}% success rate")

    final_failure_type = _classify_research_quality_failure(
        raw_candidates_count=raw_candidates,
        normalized_candidates_count=normalized_candidates,
        accepted_candidates_count=accepted_candidates,
        valid_citations_count=citation_count,
        relevance_pass_rate=float(research_metrics.get("relevance_pass_rate", 0.0) or 0.0),
        required_threshold=min_relevance_quality_gate,
        target_minimum=target_minimum,
        preprint_ratio=preprint_ratio,
        max_preprint_ratio=max_preprint_ratio,
    )
    warnings: List[str] = []
    if preprint_ratio > max_preprint_ratio:
        warnings.append("source_quality_warning: preprint_ratio_high")
    if recent_ratio < min_recent_ratio:
        warnings.append("source_quality_warning: recent_ratio_low")
    diagnostics = _build_research_diagnostics(
        topic=topic or "",
        scope=scope,
        planned_queries=research_topics or [],
        research_metrics=research_metrics,
        sources_breakdown=unique_sources_breakdown,
        citation_count=unique_valid_citations,
        final_count=citation_count,
        failure_type=final_failure_type,
        warnings=warnings,
    )

    quality_report = {
        "planner_query_count": planner_query_count,
        "executed_query_count": int(research_metrics.get("queries_executed", 0) or 0),
        "raw_hits": raw_candidates,
        "normalized_candidates": normalized_candidates,
        "accepted_candidates": accepted_candidates,
        "unique_valid_citations": unique_valid_citations,
        "final_citations": citation_count,
        "relevance_pass_rate": research_metrics.get("relevance_pass_rate", 0.0),
        "seed_count": seed_count,
        "expanded_count": expanded_count,
        "avg_relevance_score": sum(float(getattr(c, "relevance_score", 0.0) or 0.0) for c in citations) / max(1, len(citations)),
        "preprint_ratio": preprint_ratio,
        "recent_ratio": recent_ratio,
        "language_distribution": {
            "zh_title": title_zh_hits,
            "zh_lang": language_zh_hits,
        },
        "provider_error_summary": provider_error_summary,
        "rejected_reason_top5": rejected_reason_top5,
        "warnings": warnings,
        "diagnostics": diagnostics,
    }

    return {
        "citations": citations,
        "count": citation_count,
        "sources": unique_sources_breakdown,
        "failed_topics": failed_topics,
        "research_plan": research_plan,
        "metrics": research_metrics,
        "quality_report": quality_report,
    }
