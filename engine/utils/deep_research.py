#!/usr/bin/env python3
"""
ABOUTME: Autonomous deep research planner with seed reference expansion
ABOUTME: Two-phase approach: planning (generic LLM) → execution (orchestrator)
"""

import re
import json
import logging
import os
import time
import hashlib
from typing import Tuple
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from .llm_provider import create_llm_model


def _research_print(*args, **kwargs):
    """Print only if verbose research mode is enabled."""
    # Import at call time to get current value (not import-time snapshot)
    from .api_citations.orchestrator import _verbose_research
    if _verbose_research:
        print(*args, **kwargs)

# Model finish_reason codes (provider-compatible)
# 1 = STOP (normal), 2 = SAFETY, 3 = MAX_TOKENS, 4 = RECITATION
SAFETY_BLOCKED = 2

def safe_get_response_text(response) -> Tuple[str, bool]:
    """
    Safely extract text from model response, handling safety blocks.
    
    Returns:
        (text, was_blocked) - text content and whether safety filter triggered
    """
    try:
        # Check if response has candidates
        if not response.candidates:
            return "", True
        
        candidate = response.candidates[0]
        
        # Check finish_reason (2 = SAFETY block)
        if hasattr(candidate, 'finish_reason') and candidate.finish_reason == SAFETY_BLOCKED:
            return "", True
        
        # Try to get text
        return response.text.strip(), False
    except ValueError as e:
        # The response.text accessor raises ValueError on safety blocks
        if "finish_reason" in str(e):
            return "", True
        raise

logger = logging.getLogger(__name__)


class DeepResearchPlanner:
    """
    Autonomous research planner for comprehensive literature reviews.

    Uses configured LLM provider to create research strategy from seed references,
    then executes queries through citation orchestrator.

    Two-phase approach:
    1. Planning: Configured LLM autonomously plans research strategy
    2. Execution: Orchestrator runs planned queries through fallback chain

    Benefits:
    - 50+ sources vs 20-30 typical
    - Seed reference expansion
    - Autonomous gap identification
    - Systematic coverage
    """

    def __init__(
        self,
        gemini_model: Optional[Any] = None,
        llm_model: Optional[Any] = None,
        model_override: Optional[str] = None,
        api_key: Optional[str] = None,
        min_sources: int = 50,
        verbose: bool = True
    ):
        """
        Initialize deep research planner.

        Args:
            gemini_model: Backward-compatible alias for llm_model
            llm_model: Generic model adapter with generate_content() method
            model_override: Optional model name override when creating model via config
            api_key: Deprecated; kept for backward compatibility (unused in generic mode)
            min_sources: Minimum number of sources to research
            verbose: Print progress to console
        """
        _ = api_key
        self.min_sources = min_sources
        self.verbose = verbose

        # Prefer explicit generic model, then backward-compatible parameter, else factory
        self.model = llm_model or gemini_model or create_llm_model(model_override=model_override)

    def create_research_plan(
        self,
        topic: str,
        scope: Optional[str] = None,
        seed_references: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Create autonomous research plan using configured generic LLM provider."""
        if self.verbose:
            _research_print(f"\n🔍 Creating deep research plan for: {topic}")
            if scope: _research_print(f"   Scope: {scope}")

        # Build planning prompt
        prompt = self._build_planning_prompt(topic, scope, seed_references)

        try:
            max_retries = 3
            plan_text = None
            planning_timeout = 120
            target_model = os.getenv('LLM_MODEL', 'configured-model')

            for attempt in range(max_retries):
                try:
                    def _generate_with_timeout():
                        return self.model.generate_content(
                            prompt,
                            generation_config={
                                "temperature": 0.2,
                                "max_output_tokens": 8192,
                            },
                        )
                
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_generate_with_timeout)
                        try:
                            response = future.result(timeout=planning_timeout)
                            plan_text = (getattr(response, 'text', '') or '').strip()
                            if plan_text: break
                        except FuturesTimeoutError:
                            logger.warning(f"Research plan timeout (attempt {attempt + 1}/{max_retries})")
                            if attempt >= max_retries - 1: raise

                except Exception as e:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 5
                        logger.warning(f"Plan generation error, retrying in {wait_time}s: {e}")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise
            
            if not plan_text:
                raise ValueError("Unable to generate research plan content.")

            # Robust extraction: Generic models might add text around JSON
            try:
                plan = json.loads(plan_text)
            except json.JSONDecodeError:
                plan = self._extract_json_from_response(plan_text)

            # Add deterministic and explainable planning steps for observability
            plan.setdefault("topic", topic)
            if scope:
                plan.setdefault("scope", scope)
            plan["planning_logic"] = self._build_planning_logic(topic, scope, plan)

            if self.verbose:
                _research_print(f"   ✓ Plan created using {target_model}: {len(plan.get('queries', []))} queries")
                for step in plan["planning_logic"]:
                    _research_print(f"   - {step}")

            return plan

        except Exception as e:
            logger.error(f"Research planning failed: {e}")
            raise

    def _extract_json_from_response(self, text: str) -> dict:
        """
        Robustly extract JSON from LLM response.

        Handles:
        - Markdown code blocks (```json ... ```)
        - Extra text before/after JSON
        - Common JSON syntax issues
        - Non-JSON responses (generates fallback structure)

        Args:
            text: Raw LLM response text

        Returns:
            Parsed JSON as dict

        Raises:
            json.JSONDecodeError: If no valid JSON found and fallback generation fails
        """
        # Strategy 1: Try direct parse first
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # Strategy 2: Extract from markdown code blocks
        code_block_pattern = r'```(?:json)?\s*([\s\S]*?)```'
        matches = re.findall(code_block_pattern, text)
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue

        # Strategy 3: Find JSON object by matching braces
        brace_depth = 0
        start_idx = None
        for i, char in enumerate(text):
            if char == '{':
                if brace_depth == 0:
                    start_idx = i
                brace_depth += 1
            elif char == '}':
                brace_depth -= 1
                if brace_depth == 0 and start_idx is not None:
                    json_str = text[start_idx:i+1]
                    try:
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        repaired = self._repair_json(json_str)
                        try:
                            return json.loads(repaired)
                        except json.JSONDecodeError:
                            start_idx = None
                            continue

        # Strategy 4: Last resort - try with repairs on full text
        cleaned = re.sub(r'```(?:json)?', '', text)
        cleaned = cleaned.replace('```', '').strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Strategy 5: Generate fallback structure from text
        logger.warning(f"Could not parse JSON, attempting to extract queries from text")
        fallback = self._generate_fallback_from_text(text)
        if fallback:
            return fallback

        raise json.JSONDecodeError(
            f"Could not extract valid JSON from response (length: {len(text)} chars)",
            text[:500] if len(text) > 500 else text,
            0
        )

    def _generate_fallback_from_text(self, text: str) -> Optional[dict]:
        """
        Generate a fallback research plan structure from non-JSON text.

        Extracts queries by looking for:
        - Numbered lists (1. query, 2. query)
        - Bullet points (- query, * query)
        - Lines that look like search queries

        Args:
            text: Non-JSON response text

        Returns:
            Dict with queries, strategy, outline if extraction successful
        """
        queries = []

        # Look for numbered items
        numbered_pattern = r'^\s*\d+[\.\)]\s*(.+)$'
        for match in re.finditer(numbered_pattern, text, re.MULTILINE):
            query = match.group(1).strip().strip('"\'')
            if 5 < len(query) < 200:
                queries.append(query)

        # Look for bullet points
        bullet_pattern = r'^\s*[-*•]\s*(.+)$'
        for match in re.finditer(bullet_pattern, text, re.MULTILINE):
            query = match.group(1).strip().strip('"\'')
            if 5 < len(query) < 200 and query not in queries:
                queries.append(query)

        # Look for quoted strings (potential queries)
        quoted_pattern = r'"([^"]{10,150})"'
        for match in re.finditer(quoted_pattern, text):
            query = match.group(1).strip()
            if query not in queries:
                queries.append(query)

        if len(queries) >= 5:
            logger.info(f"Generated fallback plan with {len(queries)} queries from text")
            return {
                "queries": queries[:100],
                "strategy": "Fallback strategy generated from text response",
                "outline": "Section headings to be determined from research results"
            }

        return None

    def build_structured_fallback_plan(self, topic: str, scope: Optional[str] = None) -> Dict[str, Any]:
        """Deterministic fallback plan when LLM planning fails.

        Avoids low-quality noise queries such as appending arbitrary numerals or generic
        English tails to Chinese topics.
        """
        topic_text = (topic or "").strip()
        scope_text = (scope or "").strip()
        is_chinese = bool(re.search(r'[\u4e00-\u9fff]', topic_text + scope_text))

        queries: List[str] = []

        def add(q: str):
            q = (q or "").strip()
            if q and q not in queries:
                queries.append(q)

        add(topic_text)
        if scope_text and scope_text != topic_text:
            add(f"{topic_text} {scope_text}")

        if is_chinese:
            base_terms = [t for t in re.split(r'[、，,；;\s与和及]', topic_text) if t.strip()]
            base_terms = [t.strip() for t in base_terms if len(t.strip()) >= 2]
            joined = " ".join(base_terms) if base_terms else topic_text
            zh_templates = [
                "{topic}",
                "{topic} 实证研究",
                "{topic} 机制研究",
                "{topic} 路径研究",
                "{topic} 评价研究",
                "{topic} 影响因素",
                "{topic} 区域发展",
                "{topic} 产业升级",
                "{topic} 中国",
                "{topic} 政策",
            ]
            for tmpl in zh_templates:
                add(tmpl.format(topic=topic_text))
            if joined and joined != topic_text:
                add(joined)

            # Lightweight bilingual mapping for common Chinese macro/industry topics
            mapping = {
                "新质生产力": ["new quality productive forces", "productive forces upgrading"],
                "数字经济": ["digital economy", "digital transformation economy"],
                "高质量发展": ["high-quality development"],
                "产业升级": ["industrial upgrading"],
                "数字化转型": ["digital transformation"],
            }
            en_terms: List[str] = []
            for zh, ens in mapping.items():
                if zh in topic_text:
                    en_terms.extend(ens)
            if not en_terms and base_terms:
                en_terms.extend(base_terms)

            if en_terms:
                add(" AND ".join(f'"{t}"' for t in en_terms[:2]))
                add(f'China AND {" AND ".join(f"\"{t}\"" for t in en_terms[:2])}')
                add(f'{" AND ".join(f"\"{t}\"" for t in en_terms[:2])} empirical study')
                add(f'{" AND ".join(f"\"{t}\"" for t in en_terms[:2])} literature review')
        else:
            add(f"{topic_text} empirical study")
            add(f"{topic_text} literature review")
            add(f"{topic_text} framework")
            add(f"{topic_text} policy")

        # Ensure a healthy but bounded query set
        queries = queries[:30]
        return {
            "topic": topic_text,
            "scope": scope_text or None,
            "strategy": "Deterministic fallback strategy generated without LLM planning; emphasizes bilingual and methodology-aware retrieval.",
            "queries": queries,
            "outline": "1. Concept definition\n2. Core literature\n3. Mechanisms / pathways\n4. Empirical evidence\n5. Policy and future directions",
            "planning_logic": [
                "Fallback planning activated because LLM planning was unavailable.",
                f"Language mode detected: {'Chinese/bilingual' if is_chinese else 'non-Chinese'}.",
                f"Generated {len(queries)} deterministic high-signal queries without noisy suffix expansion.",
            ],
            "fallback_plan_id": hashlib.md5("|".join(queries).encode("utf-8")).hexdigest()[:12],
        }

    def _repair_json(self, json_str: str) -> str:
        """
        Attempt to repair common JSON syntax issues.

        Args:
            json_str: Potentially malformed JSON string

        Returns:
            Repaired JSON string
        """
        # Fix trailing commas in arrays/objects
        repaired = re.sub(r',\s*([}\]])', r'\1', json_str)

        # Fix missing commas between array elements (common with multi-line)
        repaired = re.sub(r'"\s*\n\s*"', '",\n"', repaired)

        # Fix unquoted keys (simple cases)
        repaired = re.sub(r'{\s*(\w+)\s*:', r'{"\1":', repaired)
        repaired = re.sub(r',\s*(\w+)\s*:', r',"\1":', repaired)

        return repaired

    def _build_planning_prompt(
        self,
        topic: str,
        scope: Optional[str],
        seed_references: Optional[List[str]]
    ) -> str:
        """Build planning prompt for a generic LLM adapter."""

        is_chinese_topic = bool(re.search(r'[\u4e00-\u9fff]', (topic or "") + " " + (scope or "")))

        prompt = f"""You are a systematic research planning assistant.

**Topic:** {topic}
"""

        if scope:
            prompt += f"**Scope:** {scope}\n"

        prompt += f"""
**Task:** Create a comprehensive research plan to find {self.min_sources}+ high-quality sources.

**Instructions:**
1. Plan research strategy (what to search for; which source types to prioritize)
2. Generate specific research queries (author:term, title:keyword, topic phrases)
3. Draft structured outline with section headings for evidence-based report

**Quality Requirements:**
- Minimum {self.min_sources} primary sources (peer-reviewed journals, standards, regulations)
- **Source Diversity:** Balance academic AND industry sources for comprehensive coverage
  - Academic: Peer-reviewed journals, conference papers, dissertations
  - Industry: Consulting reports (McKinsey, Gartner, BCG), think tanks (Brookings, RAND), regulatory bodies (WHO, OECD, European Commission), standards (ISO, IEEE)
- Avoid: Blogs, press releases, marketing materials (unless no alternative)
- Include: Recent work (last 5 years) AND foundational papers
- Coverage: Multiple perspectives, interdisciplinary if relevant

"""

        # Add seed references if provided
        if seed_references and len(seed_references) > 0:
            prompt += "**Seed References** (expand from these):\n"
            for ref in seed_references:
                prompt += f"- {ref}\n"
            prompt += "\nUse these as starting points. Find related work, citing papers, and recent developments.\n\n"

        prompt += """**Output Format:**
Return JSON with keys:
- strategy: Brief research strategy description (2-3 paragraphs)
- queries: List of specific search queries to execute (aim for 100)
- outline: Structured outline with section headings

**Query Diversity:** Generate mix of academic AND industry queries for source diversity:

Academic-focused queries (route to Crossref/Semantic Scholar):
- "peer-reviewed studies on [topic]"
- "systematic review of [topic]"
- "author:Smith [topic]"
- "title:empirical analysis [topic]"
- "meta-analysis [topic]"

Industry-focused queries (route to web-search sources):
- "McKinsey report [topic]"
- "Gartner analysis [topic]"
- "WHO guidelines [topic]"
- "OECD [topic] framework"
- "European Commission [topic] regulation"
- "BCG white paper [topic]"
- "IEEE standards [topic]"
- "NIST [topic] best practices"

Return ONLY valid JSON, no markdown blocks or explanations.
"""

        if is_chinese_topic:
            prompt += """

**Language Policy (Chinese Topic):**
- This is a Chinese topic. Use cross-lingual dual-track queries.
- Generate BOTH:
  1) Chinese academic queries (核心概念 + 方法词，如“实证研究/机制研究/文献综述”)
  2) English counterpart queries for international databases (Crossref/OpenAlex/Semantic Scholar)
- Keep bilingual pairs semantically aligned.
- Ensure at least 40% of total queries are Chinese-language.
"""
        else:
            prompt += """

**Language Policy (Non-Chinese Topic):**
- Keep queries in the original non-Chinese language context.
- Do NOT force Chinese/English dual-track expansion.
"""

        return prompt

    def _build_planning_logic(self, topic: str, scope: Optional[str], plan: Dict[str, Any]) -> List[str]:
        """Return concise, deterministic planning logic summary for display/debugging."""
        queries = plan.get("queries", [])
        return [
            f"Step 1: Define topic and constraints (topic='{topic[:80]}', scope={'provided' if scope else 'not provided'}).",
            "Step 2: Generate diverse search queries covering academic, industry, policy, and standards sources.",
            f"Step 3: Ensure enough retrieval breadth (generated {len(queries)} queries; target >= 10).",
            f"Step 4: Estimate source coverage (heuristic estimate: {self.estimate_coverage(queries)}).",
            "Step 5: Produce report-ready outputs: strategy narrative + structured outline.",
        ]


    def _rephrase_topic_for_safety(self, topic: str) -> str:
        """
        Rephrase topic to avoid triggering safety filters.
        
        When model safety filters block a topic, this method attempts to
        rephrase it in a more neutral, academic way that is less likely
        to trigger content filters.
        
        Args:
            topic: Original research topic
            
        Returns:
            Rephrased topic string
        """
        # Common problematic phrases and their safer alternatives
        replacements = [
            (r"\b(hack|hacking|hacker)\b", "security vulnerability analysis"),
            (r"\b(attack|attacking)\b", "threat assessment"),
            (r"\b(exploit|exploiting|exploitation)\b", "vulnerability"),
            (r"\b(weapon|weapons|weaponize)\b", "defense system"),
            (r"\b(kill|killing|death)\b", "impact"),
            (r"\b(drug|drugs)\b", "pharmaceutical"),
            (r"\b(terror|terrorism|terrorist)\b", "security threat"),
        ]
        
        rephrased = topic
        for pattern, replacement in replacements:
            rephrased = re.sub(pattern, replacement, rephrased, flags=re.IGNORECASE)
        
        # Add academic framing if not already present
        academic_prefixes = ["systematic review of", "academic analysis of", "literature review on", "empirical study of"]
        has_prefix = any(rephrased.lower().startswith(p) for p in academic_prefixes)
        
        if not has_prefix:
            rephrased = f"Academic literature review on {rephrased}"
        
        return rephrased

    def estimate_coverage(self, queries: List[str]) -> int:
        """
        Estimate number of sources likely to be found.

        Heuristic:
        - Specific queries (author:X, title:Y): ~1-2 sources each
        - Topic queries: ~2-5 sources each
        - Broad queries: ~5-10 sources each

        Args:
            queries: List of research queries

        Returns:
            Estimated source count
        """
        estimate = 0
        for query in queries:
            if 'author:' in query or 'title:' in query:
                estimate += 1.5  # Specific queries
            elif len(query.split()) <= 3:
                estimate += 3  # Topic queries
            else:
                estimate += 6  # Broad queries

        return int(estimate)

    def validate_plan(self, plan: Dict[str, Any]) -> bool:
        """
        Validate research plan meets quality requirements.

        Args:
            plan: Research plan from create_research_plan()

        Returns:
            True if plan is valid, False otherwise
        """
        # Check required keys
        if not all(k in plan for k in ['queries', 'outline', 'strategy']):
            logger.warning("Plan missing required keys")
            return False

        # Check query count
        queries = plan.get('queries', [])
        if len(queries) < 10:
            logger.warning(f"Too few queries: {len(queries)} < 10")
            return False

        # Estimate coverage
        estimated = self.estimate_coverage(queries)
        if estimated < self.min_sources * 0.7:  # 70% of target
            logger.warning(
                f"Estimated coverage too low: {estimated} < {self.min_sources * 0.7}"
            )
            return False

        return True

    def refine_plan(
        self,
        plan: Dict[str, Any],
        feedback: str
    ) -> Dict[str, Any]:
        """
        Refine research plan based on feedback.

        Args:
            plan: Original research plan
            feedback: Feedback on what to improve

        Returns:
            Refined research plan
        """
        if self.verbose:
            _research_print(f"\n🔄 Refining research plan...")

        prompt = f"""You are refining a research plan based on feedback.

**Original Plan:**
{json.dumps(plan, indent=2)}

**Feedback:**
{feedback}

**Task:** Improve the plan addressing the feedback while maintaining quality requirements.

Return updated JSON with same structure (strategy, queries, outline).
Return ONLY valid JSON, no markdown blocks.
"""

        try:
            response = self.model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.3,
                    "max_output_tokens": 8192,
                },
            )

            plan_text = (getattr(response, 'text', '') or '').strip()
            if not plan_text:
                return plan
            try:
                refined_plan = json.loads(plan_text)
            except json.JSONDecodeError:
                refined_plan = self._extract_json_from_response(plan_text)

            refined_plan["planning_logic"] = self._build_planning_logic(
                topic=plan.get("topic", "unknown-topic"),
                scope=plan.get("scope"),
                plan=refined_plan,
            )

            if self.verbose:
                _research_print(f"   ✓ Plan refined: {len(refined_plan.get('queries', []))} queries")

            return refined_plan

        except Exception as e:
            logger.error(f"Plan refinement failed: {e}")
            return plan  # Return original if refinement fails
