#!/usr/bin/env python3
"""
ABOUTME: Compose phase — 7 Crafter agents writing thesis sections
ABOUTME: Introduction, Literature Review, Methodology, Results, Discussion, Conclusion, Appendices
"""

import time
import logging
import traceback
import os
import re

from .context import DraftContext
from utils.text_utils import normalize_language_code

logger = logging.getLogger(__name__)


EMPIRICAL_METHOD_PATTERNS = [
    (r'本研究采用访谈法', '本文采用文献分析与理论分析相结合的方式'),
    (r'基于问卷调查', '基于已有文献与二手资料的比较讨论'),
    (r'实证分析结果表明', '文献比较与理论分析表明'),
    (r'通过收集数据', '通过梳理已有研究与二手资料'),
    (r'样本数据来源于[^。；\n]*', '论述材料主要来自既有文献、公开研究与二手资料'),
    (r'\binterview-based\b', 'literature-based'),
    (r'\binterviews?\b', 'secondary-source analysis'),
    (r'\bsurvey-based\b', 'literature-based'),
    (r'\bsurveys?\b', 'secondary discussion'),
    (r'\bquestionnaire\b', 'secondary-source review'),
    (r'\bempirical\b', 'conceptual'),
    (r'\bcollecting data\b', 'reviewing existing literature and secondary materials'),
    (r'\bsample data (?:comes?|came) from\b', 'the discussion draws on prior literature and secondary materials from'),
]


def _has_explicit_empirical_data(ctx: DraftContext) -> bool:
    """Return True only when explicit empirical inputs are present."""
    sample_size = getattr(ctx, 'sample_size', None)
    has_sample_size = False
    if isinstance(sample_size, (int, float)):
        has_sample_size = sample_size > 0
    elif isinstance(sample_size, str):
        has_sample_size = bool(sample_size.strip())

    return any([
        bool(getattr(ctx, 'data_source', None)),
        bool(getattr(ctx, 'dataset', None)),
        has_sample_size,
    ])


def _resolve_method_guard(ctx: DraftContext) -> str:
    """Configure methodology generation mode based on available data inputs."""
    explicit_empirical_inputs = _has_explicit_empirical_data(ctx)
    ctx.no_data_available = not explicit_empirical_inputs

    if ctx.no_data_available:
        ctx.force_method_type = "conceptual / literature-based"
        ctx.method_type = "conceptual"
        return """
**METHOD GUARD (MANDATORY):**
- No explicit data_source / sample_size / dataset input is available in this draft.
- Therefore, you MUST frame the methodology as **conceptual / literature-based**.
- Preferred methodology labels: **literature-based analysis**, **theoretical analysis**, **conceptual framework construction**, **secondary discussion**.
- DO NOT generate or imply empirical fieldwork, surveys, interviews, questionnaires, sample recruitment, data collection, dataset descriptions, or claimed empirical results.
- Forbidden examples include:
  - “本研究采用访谈法”
  - “基于问卷调查”
  - “实证分析结果表明”
  - “通过收集数据”
  - “样本数据来源于…”
  - “This study adopts interviews”
  - “Based on a survey”
  - “Empirical analysis shows”
  - “By collecting data”
  - “Sample data were drawn from ...”
- Instead, explicitly describe the section as a conceptual, literature-based, theoretically grounded methodological discussion.
"""

    ctx.force_method_type = None
    ctx.method_type = "empirical"
    return """
**METHOD GUARD (MANDATORY):**
- Empirical methodology is allowed only because explicit structured data inputs are available.
- If you mention dataset, sample, respondents, interviews, surveys, or empirical analysis, tie them to the provided inputs only.
- Do NOT invent missing evidence details.
"""


def _sanitize_methodology_output(text: str, conceptual_mode: bool) -> str:
    """Remove empirical wording when the run lacks explicit data inputs."""
    if not conceptual_mode or not text:
        return text

    sanitized = text
    for pattern, replacement in EMPIRICAL_METHOD_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)

    return sanitized


def _heading_policy_for(section: str) -> int:
    """Return max Markdown heading depth by generated section."""
    return {
        "abstract": 0,
        "introduction": 2,
        "main_body": 3,
        "literature_review": 3,
        "methodology": 3,
        "results": 3,
        "discussion": 3,
        "conclusion": 2,
        "references": 1,
    }.get(section, 3)


def _sanitize_section_headings(text: str, section: str) -> str:
    """Enforce writing-stage heading depth and renumber kept numbered headings."""
    max_depth = _heading_policy_for(section)
    if max_depth <= 0:
        return re.sub(r"(?m)^#{1,6}\s+(.+?)\s*$", lambda m: f"**{m.group(1).strip()}**", text)

    counters = [0] * max_depth
    last_parent: dict[int, tuple[int, ...]] = {}
    out: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            out.append(line)
            continue
        hashes, heading = match.groups()
        depth = len(hashes)
        clean_heading = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading.strip()).strip()
        if depth > max_depth:
            out.append(f"**{clean_heading}**")
            continue
        if re.match(r"^\d+(?:\.\d+)*\.?\s+", heading.strip()):
            existing_parts = [int(part) for part in re.findall(r"\d+", heading.strip().split()[0])]
            if depth == 1:
                counters[0] = existing_parts[0] if existing_parts else counters[0] + 1
            else:
                for idx in range(depth - 1):
                    if idx < len(existing_parts):
                        counters[idx] = existing_parts[idx]
                    elif counters[idx] == 0:
                        counters[idx] = 1
                parent = tuple(counters[: depth - 1])
                if last_parent.get(depth) != parent:
                    counters[depth - 1] = 0
                    last_parent[depth] = parent
                counters[depth - 1] += 1
            for idx in range(depth, max_depth):
                counters[idx] = 0
            number = ".".join(str(num) for num in counters[:depth] if num > 0)
            if number:
                suffix = "." if depth == 1 else ""
                out.append(f"{'#' * depth} {number}{suffix} {clean_heading}")
                continue
        out.append(f"{'#' * depth} {clean_heading}")
    return "\n".join(out)


def _save_sanitized_section(ctx: DraftContext, filename: str, section: str, attr: str) -> None:
    sanitized = _sanitize_section_headings(getattr(ctx, attr, "") or "", section)
    sanitized = _enforce_body_section_numbering(sanitized, section, ctx.language)
    setattr(ctx, attr, sanitized)
    (ctx.folders['drafts'] / filename).write_text(sanitized, encoding="utf-8")


BODY_SECTION_SPECS = {
    "literature_review": ("2.1", {"zh": "文献综述", "en": "Literature Review"}),
    "methodology": ("2.2", {"zh": "研究方法", "en": "Methodology"}),
    "results": ("2.3", {"zh": "分析与结果", "en": "Analysis and Results"}),
    "discussion": ("2.4", {"zh": "讨论", "en": "Discussion"}),
}


def _body_section_title(section: str, language: str) -> str:
    _, labels = BODY_SECTION_SPECS[section]
    return labels.get(normalize_language_code(language), labels["en"])


def _enforce_body_section_numbering(text: str, section: str, language: str) -> str:
    """Force split body sections to use their assigned 2.x namespace."""
    if section not in BODY_SECTION_SPECS:
        return text

    parent_number, _ = BODY_SECTION_SPECS[section]
    parent_title = _body_section_title(section, language)
    lines = text.strip().splitlines()
    out: list[str] = []
    saw_parent = False
    child_counter = 0

    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not heading:
            out.append(line)
            continue

        hashes, raw_title = heading.groups()
        level = len(hashes)
        clean_title = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", raw_title.strip()).strip()

        if not saw_parent and level <= 2:
            out.append(f"## {parent_number} {parent_title}")
            saw_parent = True
            continue

        if level == 2 and saw_parent:
            child_counter += 1
            out.append(f"### {parent_number}.{child_counter} {clean_title}")
            continue

        if level == 3:
            child_counter += 1
            out.append(f"### {parent_number}.{child_counter} {clean_title}")
            continue

        out.append(line)

    if not saw_parent:
        out.insert(0, f"## {parent_number} {parent_title}")

    return "\n".join(out).strip()


def validate_main_body_outline(content: str) -> None:
    """Validate the merged 02_main_body.md before it can enter compilation."""
    errors: list[str] = []
    expected = ["2.1", "2.2", "2.3", "2.4"]
    second_level: list[tuple[int, str, str]] = []
    current_parent: str | None = None

    for line_no, line in enumerate(content.splitlines(), start=1):
        parent = re.match(r"^##\s+2\.(\d)\.?\s+(.+?)\s*$", line)
        if parent:
            current_parent = f"2.{parent.group(1)}"
            second_level.append((line_no, current_parent, parent.group(2).strip()))
            continue

        child = re.match(r"^###\s+2\.(\d)\.(\d+)\.?\s+(.+?)\s*$", line)
        if child:
            child_parent = f"2.{child.group(1)}"
            if current_parent is None:
                errors.append(f"Line {line_no}: subsection {child_parent}.{child.group(2)} appears before a ## parent.")
            elif child_parent != current_parent:
                errors.append(
                    f"Line {line_no}: subsection {child_parent}.{child.group(2)} does not match parent {current_parent}."
                )

    observed = [number for _, number, _ in second_level]
    if observed.count("2.1") > 1:
        errors.append("02_main_body.md contains multiple ## 2.1 headings.")
    if observed != expected:
        errors.append(f"02_main_body.md must contain ## 2.1, ## 2.2, ## 2.3, ## 2.4 in order; found {observed}.")

    if errors:
        raise ValueError("Invalid 02_main_body.md outline: " + "; ".join(errors))


SECTION_LABELS = {
    'zh': {
        'introduction': '引言',
        'literature_review': '文献综述',
        'methodology': '研究方法',
        'results': '分析与结果',
        'discussion': '讨论',
        'main_body': '正文',
        'conclusion': '结论',
        'appendices': '附录',
    },
    'en': {
        'introduction': 'Introduction',
        'literature_review': 'Literature Review',
        'methodology': 'Methodology',
        'results': 'Analysis and Results',
        'discussion': 'Discussion',
        'main_body': 'Main Body',
        'conclusion': 'Conclusion',
        'appendices': 'Appendices',
    },
}


def _label(ctx: DraftContext, key: str) -> str:
    lang = 'zh' if normalize_language_code(ctx.language) == 'zh' else 'en'
    return SECTION_LABELS[lang][key]


def _compose_delay(ctx: DraftContext) -> None:
    """Compose-phase throttling is disabled by default to reduce wall-clock time."""
    from utils.agent_runner import rate_limit_delay
    if os.getenv("COMPOSE_RATE_LIMIT_DELAY", "0").strip() not in {"", "0", "false", "False"}:
        rate_limit_delay(float(os.getenv("COMPOSE_RATE_LIMIT_DELAY", "0.0")))


def run_compose_phase(ctx: DraftContext) -> None:
    """
    Execute the compose phase: 7 sequential Crafter agents.

    Mutates ctx: intro_output, lit_review_output, methodology_output,
                 results_output, discussion_output, body_output,
                 conclusion_output, appendix_output
    """
    from utils.agent_runner import run_agent

    logger.info("=" * 80)
    logger.info("PHASE 3: COMPOSE - Writing chapters")
    logger.info("=" * 80)

    if ctx.verbose:
        print("\n\u270d\ufe0f  PHASE 3: COMPOSE")

    if ctx.tracker:
        ctx.tracker.log_activity("\u270d\ufe0f Starting chapter composition", event_type="milestone", phase="writing")
        ctx.tracker.update_phase("writing", progress_percent=35, chapters_count=0, details={"stage": "starting_composition"})
        ctx.tracker.check_cancellation()
        ctx.tracker.send_heartbeat()

    _write_introduction(ctx)
    _compose_delay(ctx)

    _write_literature_review(ctx)
    _compose_delay(ctx)

    _write_methodology(ctx)
    _compose_delay(ctx)

    _write_results(ctx)
    _compose_delay(ctx)

    _write_discussion(ctx)

    _merge_body_sections(ctx)
    _compose_delay(ctx)

    _write_conclusion(ctx)
    _compose_delay(ctx)

    _write_appendices(ctx)
    _compose_delay(ctx)


# ---------------------------------------------------------------------------
# Private helpers — each constructs the prompt, calls run_agent, returns output
# ---------------------------------------------------------------------------


def _write_introduction(ctx: DraftContext) -> None:
    from utils.agent_runner import run_agent

    intro_target = ctx.word_targets['introduction']
    logger.info("[CHAPTER 1/4] Starting Introduction")
    chapter_start = time.time()

    try:
        if ctx.tracker:
            ctx.tracker.log_activity("\u270d\ufe0f Writing Introduction chapter...", event_type="writing", phase="writing")

        ctx.intro_output = run_agent(
            model=ctx.model,
            name="Crafter - Introduction",
            prompt_path="prompts/03_compose/crafter.md",
            user_input=f"""Write Introduction:

Topic: {ctx.topic}

Outline:
{ctx.formatter_output[:1200]}{ctx.citation_summary}

**CRITICAL REQUIREMENTS:**
1. Write {intro_target} words minimum
2. **Heading depth:** Use only # and ##. Never output ###, ####, or #####.
3. **Introduction scope:** Cover only background, problem, significance, research object, contributions, and paper structure.
4. Do not place theory review, performance mechanism analysis, ESG/digitalization frameworks, or complex mechanism tables in the Introduction; move those topics to Literature Review or Main Body.
5. Tables are optional in Introduction. Prefer no table; if essential, include at most 1 table.
6. **Table constraints**: Maximum 300 chars per cell, maximum 4 columns
7. Put table details in prose paragraphs AFTER tables, not inside cells{ctx.language_instruction}""",
            save_to=ctx.folders['drafts'] / "01_introduction.md",
            skip_validation=ctx.skip_validation,
            verbose=ctx.verbose,
            token_tracker=ctx.token_tracker,
            token_stage="crafter_introduction",
        )
        _save_sanitized_section(ctx, "01_introduction.md", "introduction", "intro_output")

        if ctx.tracker:
            ctx.tracker.log_activity("\u2705 Introduction complete", event_type="complete", phase="writing")

        chapter_time = time.time() - chapter_start
        logger.info(f"[CHAPTER 1/4] \u2705 Complete in {chapter_time:.1f}s")

    except Exception as e:
        logger.error(f"[CHAPTER 1/4] \u274c FAILED: {e}")
        logger.error(f"[TRACEBACK] {traceback.format_exc()}")
        if ctx.tracker:
            ctx.tracker.mark_failed(f"Chapter 1 failed: {e}")
        raise

    # MILESTONE: Introduction Complete
    if ctx.streamer:
        ctx.streamer.stream_chapter_complete(
            chapter_num=1,
            chapter_name=_label(ctx, "introduction"),
            chapter_path=ctx.folders['drafts'] / "01_introduction.md",
        )

    if ctx.tracker:
        ctx.tracker.update_phase("writing", progress_percent=40, chapters_count=1, details={"stage": "introduction_complete", "milestone": "introduction_complete"})


def _write_literature_review(ctx: DraftContext) -> None:
    from utils.agent_runner import run_agent

    lit_review_target = ctx.word_targets['literature_review']
    logger.info("[SECTION 2.1/4] Starting Literature Review")
    section_start = time.time()

    try:
        if ctx.tracker:
            ctx.tracker.log_activity("\u270d\ufe0f Writing Literature Review section...", event_type="writing", phase="writing")

        ctx.lit_review_output = run_agent(
            model=ctx.model,
            name="Crafter - Literature Review",
            prompt_path="prompts/03_compose/crafter.md",
            user_input=f"""Write section 2.1 Literature Review for this draft.

Topic: {ctx.topic}

Research summaries and abstracts:
{ctx.scribe_output[:2200]}

{ctx.citation_summary}

Outline context:
{ctx.formatter_output[:1200]}

**CRITICAL REQUIREMENTS:**

1. **Section numbering:** Start with ## 2.1 Literature Review
2. **Subsections:** Use ### 2.1.1, ### 2.1.2, etc. (at least 3 subsections)
3. **Word count:** {lit_review_target} words minimum
4. **Tables:** Include at least 1-2 comparison tables (e.g., Author vs. Findings)
   - **Maximum 300 characters per cell** - keep cells concise!
   - **Maximum 5 columns** per table
   - Put details in prose AFTER the table, not inside cells
5. **Citations:** Use {{cite_XXX}} format from citation database
6. **Depth:** Use only ## and ### in generated body sections. Never output #### or #####; turn lower-level points into bold lead phrases inside paragraphs.
7. **Cross-language evidence:** If English citations are available, explicitly use relevant English-language literature in this section instead of relying only on Chinese citations.

**CITATION-CLAIM VERIFICATION (V3 feature):**
- Before using a citation, verify it actually supports your claim
- Check the citation's title/abstract matches the topic you're citing it for
- Do NOT cite a paper about "X" to support a claim about "Y"
- Example: A paper about "creatine supplementation" should NOT be cited for a claim about "caffeine effects"
- If unsure whether a citation supports a claim, rephrase the claim to match what the citation actually covers

**Content to cover:**
- Theoretical framework and foundational concepts
- Review of empirical studies (with abstracts provided)
- Comparison of different approaches/methodologies
- Evolution of the field
- Research gaps that your draft will address

**Use the abstracts provided to write evidence-based literature review with specific findings, NOT generic statements.**{ctx.language_instruction}""",
            save_to=ctx.folders['drafts'] / "02_1_literature_review.md",
            skip_validation=ctx.skip_validation,
            verbose=ctx.verbose,
            token_tracker=ctx.token_tracker,
            token_stage="crafter_literature_review",
        )
        _save_sanitized_section(ctx, "02_1_literature_review.md", "literature_review", "lit_review_output")

        section_time = time.time() - section_start
        logger.info(f"[SECTION 2.1/4] \u2705 Complete in {section_time:.1f}s")

        if ctx.tracker:
            ctx.tracker.log_activity("\u2705 Literature Review complete", event_type="complete", phase="writing")
            ctx.tracker.update_phase("writing", progress_percent=45, chapters_count=2, details={"stage": "literature_review_complete"})

        if ctx.streamer:
            ctx.streamer.stream_chapter_complete(
                chapter_num=2,
                chapter_name=f"{_label(ctx, 'literature_review')}（2.1）",
                chapter_path=ctx.folders['drafts'] / "02_1_literature_review.md",
            )

    except Exception as e:
        logger.error(f"[SECTION 2.1/4] \u274c FAILED: {e}")
        logger.error(f"[TRACEBACK] {traceback.format_exc()}")
        if ctx.tracker:
            ctx.tracker.mark_failed(f"Section 2.1 (Literature Review) failed: {e}")
        raise


def _write_methodology(ctx: DraftContext) -> None:
    from utils.agent_runner import run_agent

    methodology_target = ctx.word_targets['methodology']
    logger.info("[SECTION 2.2/4] Starting Methodology")
    section_start = time.time()
    method_guard = _resolve_method_guard(ctx)

    try:
        if ctx.tracker:
            ctx.tracker.log_activity("\u270d\ufe0f Writing Methodology section...", event_type="writing", phase="writing")

        ctx.methodology_output = run_agent(
            model=ctx.model,
            name="Crafter - Methodology",
            prompt_path="prompts/03_compose/crafter.md",
            user_input=f"""Write section 2.2 Methodology for this draft.

Topic: {ctx.topic}

Literature Review context (what was identified):
{ctx.lit_review_output[-1200:]}

Research gaps from Signal phase:
{ctx.signal_output[:1500]}

Outline:
{ctx.formatter_output[:1000]}

{ctx.citation_summary}

{method_guard}

**CRITICAL REQUIREMENTS:**

1. **Section numbering:** Start with ## 2.2 Methodology
2. **Subsections:** Use ### 2.2.1, ### 2.2.2, etc. (at least 2-3 subsections)
3. **Depth:** Use only ## and ###. Never output #### or #####; convert lower-level points into bold lead phrases inside paragraphs.
4. **Word count:** {methodology_target} words minimum
5. **Tables:** Include at least 1 methodology summary table
   - **Maximum 300 characters per cell** - keep cells concise!
   - **Maximum 5 columns** per table
   - Put details in prose AFTER the table, not inside cells
6. **Build on Literature Review:** Reference gaps identified in section 2.1
7. **Citations:** ONLY use citations from the CITATION DATABASE above with {{cite_XXX}} format
8. If English citations are available, include relevant English-language methodology literature where appropriate.

**CITATION-CLAIM VERIFICATION:**
- Before using a citation, verify it actually supports your claim
- Check the citation's title/abstract matches the methodology you're describing
- Do NOT cite a paper about "X methodology" to describe "Y methodology"
- If unsure, rephrase to match what the citation actually covers

**\U0001f6a8 CRITICAL ANTI-HALLUCINATION RULES:**
- **NEVER claim "we conducted studies"** - This is a literature review draft, not an empirical study
- **NEVER invent datasets** (e.g., "Dataset X-500", "we analyzed 10,000 samples")
- **NEVER fabricate experimental procedures** (e.g., "we ran experiments on...")
- **ONLY describe methodologies from cited literature** - Use "Previous research {{cite_XXX}} used..." not "We used..."
- **Use hypothetical/theoretical language** for proposed approaches: "A potential methodology might involve..." not "We implemented..."
- **Focus on synthesizing existing research methods**, not claiming to have conducted new research

**Content to cover:**
- Research design and approach - prioritize literature-based analysis, theoretical analysis, conceptual framework construction, or secondary discussion unless explicit data inputs are present
- Evidence base and source discussion - describe cited literature and secondary materials rather than invented primary data when no explicit dataset/sample/data source is provided
- Analysis framework/techniques - from existing research
- Rationale for chosen methods (connect to gaps from 2.1) - theoretical justification
- Tools and technologies used - from literature, not "we used"
- Study limitations and considerations - theoretical discussion

**Connect to Literature Review:** "To address the gap identified in section 2.1 regarding X, a potential methodology could follow approaches described in {{cite_XXX}}..."**{ctx.language_instruction}""",
            save_to=ctx.folders['drafts'] / "02_2_methodology.md",
            skip_validation=ctx.skip_validation,
            verbose=ctx.verbose,
            token_tracker=ctx.token_tracker,
            token_stage="crafter_methodology",
        )
        ctx.methodology_output = _sanitize_methodology_output(
            ctx.methodology_output,
            conceptual_mode=bool(ctx.no_data_available),
        )
        _save_sanitized_section(ctx, "02_2_methodology.md", "methodology", "methodology_output")

        section_time = time.time() - section_start
        logger.info(f"[SECTION 2.2/4] \u2705 Complete in {section_time:.1f}s")

        if ctx.tracker:
            ctx.tracker.log_activity("\u2705 Methodology complete", event_type="complete", phase="writing")
            ctx.tracker.update_phase("writing", progress_percent=50, chapters_count=2, details={"stage": "methodology_complete"})

        if ctx.streamer:
            ctx.streamer.stream_chapter_complete(
                chapter_num=2,
                chapter_name=f"{_label(ctx, 'methodology')}（2.2）",
                chapter_path=ctx.folders['drafts'] / "02_2_methodology.md",
            )

    except Exception as e:
        logger.error(f"[SECTION 2.2/4] \u274c FAILED: {e}")
        logger.error(f"[TRACEBACK] {traceback.format_exc()}")
        if ctx.tracker:
            ctx.tracker.mark_failed(f"Section 2.2 (Methodology) failed: {e}")
        raise


def _write_results(ctx: DraftContext) -> None:
    from utils.agent_runner import run_agent

    results_target = ctx.word_targets['results']
    logger.info("[SECTION 2.3/4] Starting Analysis and Results")
    section_start = time.time()

    try:
        if ctx.tracker:
            ctx.tracker.log_activity("\u270d\ufe0f Writing Analysis & Results section...", event_type="writing", phase="writing")

        ctx.results_output = run_agent(
            model=ctx.model,
            name="Crafter - Analysis and Results",
            prompt_path="prompts/03_compose/crafter.md",
            user_input=f"""Write section 2.3 Analysis and Results for this draft.

Topic: {ctx.topic}

Methodology used (from section 2.2):
{ctx.methodology_output[-1000:]}

Literature Review context (theoretical framework):
{ctx.lit_review_output[:1000]}

Research data:
{ctx.scribe_output[1000:2000]}

{ctx.citation_summary}

**CRITICAL REQUIREMENTS:**

1. **Section numbering:** Start with ## 2.3 Analysis and Results
2. **Subsections:** Use ### 2.3.1, ### 2.3.2, etc. (at least 3 subsections)
3. **Depth:** Use only ## and ###. Never output #### or #####; convert lower-level points into bold lead phrases inside paragraphs.
4. **Word count:** {results_target} words minimum
5. **Tables:** Include at least 2-3 data/results tables
   - **Maximum 300 characters per cell** - keep cells concise!
   - **Maximum 5 columns** per table
   - Put details in prose AFTER the table, not inside cells
6. **Synthesize Literature Findings:** Present results FROM CITED SOURCES, not from new research
7. **Citations:** ONLY use citations from the CITATION DATABASE above with {{cite_XXX}} format

**CITATION-CLAIM VERIFICATION:**
- Before citing a source for a finding, verify the citation actually reports that finding
- Check citation title/abstract matches the result you're attributing to it
- Do NOT cite a study about "X" to support findings about "Y"
- If unsure, rephrase to match what the citation actually found

**\U0001f6a8 CRITICAL ANTI-HALLUCINATION RULES:**
- **NEVER claim "we found", "we analyzed", "our results show"** - This is a literature review, not an empirical study
- **NEVER invent data, statistics, or results** (e.g., "we found 87% accuracy", "our analysis revealed...")
- **NEVER fabricate datasets or sample sizes** (e.g., "Dataset X-500", "we analyzed 10,000 samples")
- **ONLY present findings from cited literature** - Use "Research by {{cite_001}} found..." not "We found..."
- **ONLY use data/statistics from cited sources** - All numbers must come from {{cite_XXX}} references
- **Synthesize existing research findings**, not claim to have conducted new analysis
- **Use language like:** "Studies have shown...", "Research indicates...", "Findings suggest..." NOT "We found...", "Our analysis..."

**Content to cover:**
- Key findings FROM CITED LITERATURE (with specific data from cited abstracts/papers)
- Synthesis of data analysis and interpretation FROM EXISTING RESEARCH
- Statistical results FROM CITED STUDIES (if applicable)
- Patterns and trends observed IN THE LITERATURE
- Visual data presentation (tables summarizing findings from cited sources)
- Comparison with baseline/benchmarks FROM CITED RESEARCH

**Connect sections:** "Research applying methodologies similar to those described in section 2.2 has found..." and "These findings from the literature relate to the theoretical framework in section 2.1..."**{ctx.language_instruction}""",
            save_to=ctx.folders['drafts'] / "02_3_analysis_results.md",
            skip_validation=ctx.skip_validation,
            verbose=ctx.verbose,
            token_tracker=ctx.token_tracker,
            token_stage="crafter_results",
        )
        _save_sanitized_section(ctx, "02_3_analysis_results.md", "results", "results_output")

        section_time = time.time() - section_start
        logger.info(f"[SECTION 2.3/4] \u2705 Complete in {section_time:.1f}s")

        if ctx.tracker:
            ctx.tracker.log_activity("\u2705 Analysis & Results complete", event_type="complete", phase="writing")
            ctx.tracker.update_phase("writing", progress_percent=55, chapters_count=2, details={"stage": "results_complete"})

        if ctx.streamer:
            ctx.streamer.stream_chapter_complete(
                chapter_num=2,
                chapter_name=f"{_label(ctx, 'results')}（2.3）",
                chapter_path=ctx.folders['drafts'] / "02_3_analysis_results.md",
            )

    except Exception as e:
        logger.error(f"[SECTION 2.3/4] \u274c FAILED: {e}")
        logger.error(f"[TRACEBACK] {traceback.format_exc()}")
        if ctx.tracker:
            ctx.tracker.mark_failed(f"Section 2.3 (Analysis and Results) failed: {e}")
        raise


def _write_discussion(ctx: DraftContext) -> None:
    from utils.agent_runner import run_agent

    discussion_target = ctx.word_targets['discussion']
    logger.info("[SECTION 2.4/4] Starting Discussion")
    section_start = time.time()

    try:
        if ctx.tracker:
            ctx.tracker.log_activity("\u270d\ufe0f Writing Discussion section...", event_type="writing", phase="writing")

        ctx.discussion_output = run_agent(
            model=ctx.model,
            name="Crafter - Discussion",
            prompt_path="prompts/03_compose/crafter.md",
            user_input=f"""Write section 2.4 Discussion for this draft.

Topic: {ctx.topic}

Results (from section 2.3):
{ctx.results_output[-1200:]}

Literature Review context (to compare with):
{ctx.lit_review_output[:1000]}

Research gaps addressed:
{ctx.signal_output[:1000]}

{ctx.citation_summary}

**CRITICAL REQUIREMENTS:**

1. **Section numbering:** Start with ## 2.4 Discussion
2. **Subsections:** Use ### 2.4.1, ### 2.4.2, etc. (at least 2-3 subsections)
3. **Depth:** Use only ## and ###. Never output #### or #####; convert lower-level points into bold lead phrases inside paragraphs.
4. **Word count:** {discussion_target} words minimum
5. **Tables:** Include at least 1 summary/implications table
   - **Maximum 300 characters per cell** - keep cells concise!
   - **Maximum 5 columns** per table
   - Put details in prose AFTER the table, not inside cells
6. **Interpret Literature Findings:** Discuss findings FROM CITED SOURCES, not from new research
7. **Citations:** ONLY use citations from the CITATION DATABASE above with {{cite_XXX}} format

**\U0001f6a8 CRITICAL ANTI-HALLUCINATION RULES:**
- **NEVER claim "our results", "our findings", "we conclude"** - This is a literature review, not an empirical study
- **NEVER invent conclusions or implications** from non-existent research
- **ONLY discuss findings from cited literature** - Use "Research findings {{cite_001}} suggest..." not "Our findings suggest..."
- **Synthesize existing research**, not claim to have conducted new analysis
- **Use language like:** "The literature suggests...", "Research indicates...", "Studies have shown..." NOT "We found...", "Our analysis..."

**Content to cover:**
- Interpretation of synthesized findings FROM CITED LITERATURE
- Comparison with the prior research and theoretical context
- How findings FROM LITERATURE address research gaps
- Theoretical implications FROM EXISTING RESEARCH
- Practical implications FROM CITED STUDIES
- Limitations discussed IN THE LITERATURE
- Future research directions suggested BY EXISTING RESEARCH

**Cross-section coherence without template residue:**

- Connect back to prior chapters by topic, not by repeating mechanical labels.
- Do NOT use phrases like "literature review (section 2.1)", "findings presented in section 2.3", "文献综述（相关章节）", "相关章节指出", or repeated section-number references as grammatical subjects.
- In Chinese drafts, write natural academic prose such as "前文关于离线编程技术的梳理表明...", "现有研究对路径精度问题的讨论提示...", or "综合路径规划与仿真验证两方面的证据可见...".
- If a section reference is genuinely needed, use it sparingly and naturally, e.g. "第2章已说明..." or "第4章的比较分析显示..."; do not include more than two explicit section references in the whole Discussion.
- Make the Discussion's main subjects the paper's concepts, method framework, evidence, engineering implications, limitations, and future work, not the Literature Review chapter itself.

**Example opening:** "The synthesized evidence shows that complex-trajectory laser cutting is constrained less by path generation alone than by the coupling among geometric accuracy, robot kinematics, and process parameters. This changes how offline programming systems should be evaluated: simulation must verify not only collision-free motion, but also whether the planned trajectory remains stable under realistic process constraints."

**Remember:** Maintain academic coherence through substantive transitions and citations, not repeated section labels. ALWAYS cite sources for any findings discussed.**{ctx.language_instruction}""",
            save_to=ctx.folders['drafts'] / "02_4_discussion.md",
            skip_validation=ctx.skip_validation,
            verbose=ctx.verbose,
            token_tracker=ctx.token_tracker,
            token_stage="crafter_discussion",
        )
        _save_sanitized_section(ctx, "02_4_discussion.md", "discussion", "discussion_output")

        section_time = time.time() - section_start
        logger.info(f"[SECTION 2.4/4] \u2705 Complete in {section_time:.1f}s")

        if ctx.tracker:
            ctx.tracker.log_activity("\u2705 Discussion complete", event_type="complete", phase="writing")
            ctx.tracker.update_phase("writing", progress_percent=60, chapters_count=2, details={"stage": "discussion_complete"})

        if ctx.streamer:
            ctx.streamer.stream_chapter_complete(
                chapter_num=2,
                chapter_name=f"{_label(ctx, 'discussion')}（2.4）",
                chapter_path=ctx.folders['drafts'] / "02_4_discussion.md",
            )

    except Exception as e:
        logger.error(f"[SECTION 2.4/4] \u274c FAILED: {e}")
        logger.error(f"[TRACEBACK] {traceback.format_exc()}")
        if ctx.tracker:
            ctx.tracker.mark_failed(f"Section 2.4 (Discussion) failed: {e}")
        raise


def _merge_body_sections(ctx: DraftContext) -> None:
    """Merge all body sections into a single body_output."""
    logger.info(f"[CHAPTER 2/4] Merging 4 sections into {_label(ctx, 'main_body')}")

    if ctx.tracker:
        ctx.tracker.log_activity("🔗 Merging sections into Main Body...", event_type="info", phase="writing")

    try:
        merged_content = []
        for section_file in [
            ctx.folders['drafts'] / "02_1_literature_review.md",
            ctx.folders['drafts'] / "02_2_methodology.md",
            ctx.folders['drafts'] / "02_3_analysis_results.md",
            ctx.folders['drafts'] / "02_4_discussion.md",
        ]:
            if section_file.exists():
                content = section_file.read_text(encoding='utf-8')
                merged_content.append(content)
                merged_content.append("\n\n")

        ctx.body_output = "".join(merged_content)
        validate_main_body_outline(ctx.body_output)
        main_body_file = ctx.folders['drafts'] / "02_main_body.md"
        main_body_file.write_text(ctx.body_output, encoding='utf-8')

        logger.info(f"[CHAPTER 2/4] \u2705 Merged into {main_body_file}")

        if ctx.streamer:
            ctx.streamer.stream_chapter_complete(
                chapter_num=2,
                chapter_name=f"{_label(ctx, 'main_body')}（完整）",
                chapter_path=main_body_file,
            )

    except Exception as e:
        logger.error(f"[CHAPTER 2/4] \u274c Merge FAILED: {e}")
        logger.error(f"[TRACEBACK] {traceback.format_exc()}")
        if ctx.tracker:
            ctx.tracker.mark_failed(f"Chapter 2 merge failed: {e}")
        raise


def _write_conclusion(ctx: DraftContext) -> None:
    from utils.agent_runner import run_agent

    conclusion_target = ctx.word_targets['conclusion']
    logger.info("[CHAPTER 3/4] Starting Conclusion")
    chapter_start = time.time()

    try:
        if ctx.tracker:
            ctx.tracker.log_activity("\u270d\ufe0f Writing Conclusion chapter...", event_type="writing", phase="writing")

        ctx.conclusion_output = run_agent(
            model=ctx.model,
            name="Crafter - Conclusion",
            prompt_path="prompts/03_compose/crafter.md",
            user_input=f"""Write Conclusion:

Topic: {ctx.topic}

Main findings:
{ctx.body_output[:1200]}

{ctx.citation_summary}

**CRITICAL REQUIREMENTS:**
1. Write {conclusion_target} words minimum
2. **Heading depth:** Use only # and ##. Never output ###, ####, or #####.
3. **Conclusion numbering:** This is the final thesis chapter. For Chinese, output exactly "# 6. 结论" with "## 6.1 研究总结与管理启示" and "## 6.2 研究局限与未来展望". For English, output exactly "# 6. Conclusion" with "## 6.1 Summary and Implications" and "## 6.2 Limitations and Future Research".
4. **Forbidden conclusion numbering:** Never output "# 3. 结论", "# 4. 结论", "# 5. 结论", "# 3. Conclusion", "# 4. Conclusion", "# 5. Conclusion", or any conclusion subsection numbered 3.x, 4.x, or 5.x.
5. Include at most 1 summary table if relevant
6. **Table constraints**: Maximum 300 chars per cell, maximum 4 columns
7. Put table details in prose paragraphs AFTER tables, not inside cells
8. **Citations:** ONLY use citations from the CITATION DATABASE above with {{cite_XXX}} format{ctx.language_instruction}""",
            save_to=ctx.folders['drafts'] / "03_conclusion.md",
            skip_validation=ctx.skip_validation,
            verbose=ctx.verbose,
            token_tracker=ctx.token_tracker,
            token_stage="crafter_conclusion",
        )
        _save_sanitized_section(ctx, "03_conclusion.md", "conclusion", "conclusion_output")

        chapter_time = time.time() - chapter_start
        logger.info(f"[CHAPTER 3/4] \u2705 Complete in {chapter_time:.1f}s")

    except Exception as e:
        logger.error(f"[CHAPTER 3/4] \u274c FAILED: {e}")
        logger.error(f"[TRACEBACK] {traceback.format_exc()}")
        if ctx.tracker:
            ctx.tracker.mark_failed(f"Chapter 3 (Conclusion) failed: {e}")
        raise

    if ctx.streamer:
        ctx.streamer.stream_chapter_complete(
            chapter_num=3,
            chapter_name=_label(ctx, "conclusion"),
            chapter_path=ctx.folders['drafts'] / "03_conclusion.md",
        )

    if ctx.tracker:
        ctx.tracker.log_activity("\u2705 Conclusion complete", event_type="complete", phase="writing")
        ctx.tracker.update_phase("writing", progress_percent=70, chapters_count=3, details={"stage": "conclusion_complete", "milestone": "conclusion_complete"})


def _write_appendices(ctx: DraftContext) -> None:
    from utils.agent_runner import run_agent

    appendices_target = ctx.word_targets['appendices']
    logger.info("[CHAPTER 4/4] Starting Appendices")
    chapter_start = time.time()

    try:
        if appendices_target == '0':
            logger.info("  Skipping appendices for research paper format")
            ctx.appendix_output = ""
        else:
            ctx.appendix_output = run_agent(
                model=ctx.model,
                name="Crafter - Appendices",
                prompt_path="prompts/03_compose/crafter.md",
                user_input=f"""Write 3-4 appendices for this draft:

Topic: {ctx.topic}

Draft content summary:
- Introduction: {ctx.intro_output[:1500]}
- Main findings: {ctx.body_output[:1200]}
- Conclusion: {ctx.conclusion_output[:1000]}

{ctx.citation_summary}

**REQUIREMENTS:**
1. **Citations:** ONLY use citations from the CITATION DATABASE above with {{cite_XXX}} format
2. Generate 3-4 appendices following this structure:

## Appendix A: Conceptual Framework
A detailed framework or model relevant to the draft topic with tables/diagrams described in markdown.

## Appendix B: Supplementary Data Tables
Additional data, metrics, or case study details supporting the main analysis.

## Appendix C: Glossary of Terms
Key technical terms and definitions used throughout the draft.

## Appendix D: Additional Resources
Supplementary references, tools, and resources for further reading.

**CRITICAL REQUIREMENTS:**
1. Write {appendices_target} words total across all appendices
2. Use markdown tables where appropriate
3. **Table constraints**: Maximum 300 chars per cell, maximum 5 columns
4. Put table details in prose paragraphs AFTER tables, not inside cells
5. Each appendix should be standalone and informative{ctx.language_instruction}""",
                save_to=ctx.folders['drafts'] / "04_appendices.md",
                skip_validation=ctx.skip_validation,
                verbose=ctx.verbose,
                token_tracker=ctx.token_tracker,
                token_stage="crafter_appendices",
            )

        chapter_time = time.time() - chapter_start
        logger.info(f"[CHAPTER 4/4] \u2705 Complete in {chapter_time:.1f}s")

        if ctx.tracker:
            ctx.tracker.update_phase("writing", progress_percent=75, chapters_count=4, details={"stage": "appendices_complete"})

        if ctx.streamer and appendices_target != '0':
            ctx.streamer.stream_chapter_complete(
                chapter_num=4,
                chapter_name=_label(ctx, "appendices"),
                chapter_path=ctx.folders['drafts'] / "04_appendices.md",
            )

        logger.info("=" * 80)
        logger.info("PHASE 3 COMPLETE - All chapters written successfully!")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"[CHAPTER 4/4] \u274c FAILED: {e}")
        logger.error(f"[TRACEBACK] {traceback.format_exc()}")
        if ctx.tracker:
            ctx.tracker.mark_failed(f"Chapter 4 (Appendices) failed: {e}")
        raise
