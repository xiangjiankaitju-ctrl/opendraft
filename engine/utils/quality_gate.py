#!/usr/bin/env python3
"""
ABOUTME: Quality gate for draft output scoring
ABOUTME: Scores draft quality after compose phase, enables early exit or warnings
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from utils.text_utils import normalize_language_code

logger = logging.getLogger(__name__)


def _is_conceptual_methodology(ctx: 'DraftContext') -> bool:
    explicit_method_type = (getattr(ctx, 'method_type', None) or '').strip().lower()
    explicit_force_type = (getattr(ctx, 'force_method_type', None) or '').strip().lower()
    if 'conceptual' in explicit_method_type or 'literature' in explicit_method_type:
        return True
    if 'conceptual' in explicit_force_type or 'literature' in explicit_force_type:
        return True

    methodology_text = getattr(ctx, 'methodology_output', '') or ''
    conceptual_markers = [
        'literature-based analysis',
        'theoretical analysis',
        'conceptual framework',
        'secondary discussion',
        '文献分析',
        '理论分析',
        '概念框架',
        '二手资料',
        '二手数据',
    ]
    lowered = methodology_text.lower()
    return any(marker in lowered for marker in conceptual_markers)


@dataclass
class QualityScore:
    """Quality assessment result."""
    total_score: int  # 0-100
    word_count_score: int  # 0-25
    citation_score: int  # 0-25
    completeness_score: int  # 0-25
    structure_score: int  # 0-25
    issues: List[str]
    passed: bool = False
    hard_violations: List[str] = field(default_factory=list)


def score_draft_quality(ctx: 'DraftContext') -> QualityScore:
    """
    Score draft quality after compose phase.
    
    Scoring breakdown (100 points total):
    - Word count: 25 points (meets target lengths)
    - Citations: 25 points (proper citation density)
    - Completeness: 25 points (all sections present)
    - Structure: 25 points (proper markdown structure)
    
    Args:
        ctx: DraftContext with compose outputs
        
    Returns:
        QualityScore with breakdown and pass/fail status
    """
    issues = []
    
    # === WORD COUNT (25 points) ===
    word_count_score = _score_word_count(ctx, issues)
    
    # === CITATION DENSITY (25 points) ===
    citation_score = _score_citations(ctx, issues)
    
    # === COMPLETENESS (25 points) ===
    completeness_score = _score_completeness(ctx, issues)
    
    # === STRUCTURE (25 points) ===
    structure_score = _score_structure(ctx, issues)
    
    total_score = word_count_score + citation_score + completeness_score + structure_score
    hard_violations = _collect_hard_violations(ctx)
    passed = total_score >= 50 and not hard_violations  # Hard violations always fail
    
    return QualityScore(
        total_score=total_score,
        word_count_score=word_count_score,
        citation_score=citation_score,
        completeness_score=completeness_score,
        structure_score=structure_score,
        issues=issues,
        hard_violations=hard_violations,
        passed=passed,
    )


def _collect_hard_violations(ctx: 'DraftContext') -> List[str]:
    """Collect non-negotiable integrity violations.

    These checks catch structural and methodological issues that should block
    submission-quality output regardless of aggregate quality score.
    """
    violations: List[str] = []
    all_text = f"{ctx.intro_output}\n{ctx.body_output}\n{ctx.conclusion_output}"

    # 1) Generator residue / placeholders
    residue_patterns = [
        (r'\[VERIFY\]', "Contains [VERIFY] marker"),
        (r'crafted to meet the word count and citation requirements', "Contains generator note about word-count/citation crafting"),
        (r'\{\s*\([^)]+\)\s*\}', "Contains malformed nested citation brackets like {(Author, Year)}"),
        (r'\[MISSING:\s*[^\]]+\]', "Contains unresolved missing-citation marker"),
    ]
    for pattern, message in residue_patterns:
        if re.search(pattern, all_text, re.IGNORECASE):
            violations.append(message)

    # 2) Heading numbering integrity (e.g., 4.1 appears without prior 4)
    heading_numbers = re.findall(r'^#{1,6}\s+(\d+(?:\.\d+)*)\.?\s+.+$', all_text, re.MULTILINE)
    seen = set()
    for raw in heading_numbers:
        num = raw.strip('.')
        parts = num.split('.')
        if len(parts) > 1:
            parent = '.'.join(parts[:-1])
            if parent and parent not in seen:
                violations.append(f"Orphan numbered heading detected: {num} (missing parent {parent})")
                break
        seen.add(num)

    # 3) Duplicate table/figure caption numbering
    caption_refs = re.findall(r'^\s*(Table|Figure)\s+([A-Za-z]?\d+)\s*[:.-]', all_text, re.MULTILINE | re.IGNORECASE)
    normalized = [f"{kind.lower()} {num.lower()}" for kind, num in caption_refs]
    duplicate_captions = sorted({x for x in normalized if normalized.count(x) > 1})
    if duplicate_captions:
        violations.append(f"Duplicate table/figure caption numbering: {', '.join(duplicate_captions[:3])}")

    # 4) Method-claim vs evidence consistency
    if not _is_conceptual_methodology(ctx):
        method_claims = {
            "interview-based": {
                "claim": r'\b(mixed[- ]methods?|semi-structured interviews?|expert interviews?|interviews?)\b',
                "evidence": r'\b(n\s*=\s*\d+|participants?|respondents?|sampling|interview protocol|interview guide)\b',
            },
            "quant-model": {
                "claim": r'\b(regression analysis|ols|fixed effects|difference[- ]in[- ]differences|did model|panel data)\b',
                "evidence": r'(\bmodel\s*\(\d+\)|\by\s*=\s*|\bcoefficient\b|\bp\s*[<=>]\s*0?\.\d+|\bstandard errors?\b|\br-?squared\b)',
            },
            "thematic-coding": {
                "claim": r'\b(nvivo|thematic analysis|qualitative coding)\b',
                "evidence": r'\b(codebook|coding scheme|intercoder|kappa|themes? emerged)\b',
            },
        }
        for label, rule in method_claims.items():
            if re.search(rule["claim"], all_text, re.IGNORECASE) and not re.search(rule["evidence"], all_text, re.IGNORECASE):
                violations.append(f"Method claim/evidence mismatch: {label} claimed without required evidence details")

    # 5) Topic-concept alignment for title core terms
    topic = (getattr(ctx, 'topic', '') or '').strip()
    if topic:
        core_terms: List[str] = []
        # English-like terms
        for token in re.findall(r'[A-Za-z][A-Za-z\-]{3,}', topic.lower()):
            if token not in {'research', 'study', 'impact', 'effects', 'analysis', 'based'}:
                core_terms.append(token)
        # Chinese phrase chunks
        for chunk in re.findall(r'[\u4e00-\u9fff]{2,}', topic):
            core_terms.append(chunk)
        core_terms = list(dict.fromkeys(core_terms))[:8]

        if core_terms:
            matched = 0
            for term in core_terms:
                if re.search(re.escape(term), all_text, re.IGNORECASE):
                    matched += 1
            min_match = max(1, min(3, len(core_terms) // 2))
            if matched < min_match:
                violations.append(
                    f"Topic-concept alignment too weak: matched {matched}/{len(core_terms)} core terms from title"
                )

    return violations


def _count_words(text: str) -> int:
    """Count words in text with Chinese-aware fallback."""
    if not text:
        return 0

    whitespace_tokens = len(text.split())
    cjk_chars = len(re.findall(r'[\u4e00-\u9fff]', text))

    # Chinese academic text is often undercounted by whitespace tokenization.
    if cjk_chars >= 20:
        return max(whitespace_tokens, cjk_chars)
    return whitespace_tokens


def _score_word_count(ctx: 'DraftContext', issues: List[str]) -> int:
    """Score based on word count targets."""
    score = 0
    
    # Get minimum targets based on academic level
    min_targets = {
        'research_paper': {'intro': 400, 'body': 1500, 'conclusion': 300},
        'bachelor': {'intro': 1000, 'body': 5000, 'conclusion': 600},
        'master': {'intro': 1500, 'body': 10000, 'conclusion': 1000},
        'phd': {'intro': 2500, 'body': 20000, 'conclusion': 2000},
    }
    targets = min_targets.get(ctx.academic_level, min_targets['master'])
    
    # Introduction (8 points)
    intro_words = _count_words(ctx.intro_output)
    if intro_words >= targets['intro']:
        score += 8
    elif intro_words >= targets['intro'] * 0.5:
        score += 4
        issues.append(f"Introduction short: {intro_words} words (target: {targets['intro']})")
    else:
        issues.append(f"Introduction very short: {intro_words} words (target: {targets['intro']})")
    
    # Body (12 points)
    body_words = _count_words(ctx.body_output)
    if body_words >= targets['body']:
        score += 12
    elif body_words >= targets['body'] * 0.5:
        score += 6
        issues.append(f"Body short: {body_words} words (target: {targets['body']})")
    else:
        issues.append(f"Body very short: {body_words} words (target: {targets['body']})")
    
    # Conclusion (5 points)
    conclusion_words = _count_words(ctx.conclusion_output)
    if conclusion_words >= targets['conclusion']:
        score += 5
    elif conclusion_words >= targets['conclusion'] * 0.5:
        score += 2
        issues.append(f"Conclusion short: {conclusion_words} words (target: {targets['conclusion']})")
    else:
        issues.append(f"Conclusion very short: {conclusion_words} words (target: {targets['conclusion']})")
    
    return score


def _score_citations(ctx: 'DraftContext', issues: List[str]) -> int:
    """Score based on citation usage."""
    score = 0
    
    # Count citation references in all outputs
    all_text = ctx.intro_output + ctx.body_output + ctx.conclusion_output
    citation_refs = re.findall(r'\{cite_\d+\}', all_text)
    unique_citations = len(set(citation_refs))
    total_citations = len(citation_refs)

    available_unique = None
    used_unique_from_metrics = None
    if getattr(ctx, 'citation_metrics', None):
        available_unique = ctx.citation_metrics.get('available_unique_citations')
        used_unique_from_metrics = ctx.citation_metrics.get('used_unique_citations')

    effective_used_unique = (
        int(used_unique_from_metrics)
        if isinstance(used_unique_from_metrics, int)
        else unique_citations
    )
    
    # Get target based on academic level
    min_citations = ctx.word_targets.get('min_citations', 10)
    
    # Unique citations used (15 points)
    if effective_used_unique >= min_citations:
        score += 15
    elif effective_used_unique >= min_citations * 0.5:
        score += 8
        issue = f"Few used unique citations: {effective_used_unique} (target: {min_citations})"
        if isinstance(available_unique, int):
            issue += f"; available unique citations: {available_unique}"
        issues.append(issue)
    else:
        issue = f"Very few used unique citations: {effective_used_unique} (target: {min_citations})"
        if isinstance(available_unique, int):
            issue += f"; available unique citations: {available_unique}"
        issues.append(issue)
    
    # Citation density (10 points) - at least 1 citation per 500 words
    word_count = _count_words(all_text)
    expected_density = max(1, word_count // 500)
    if total_citations >= expected_density:
        score += 10
    elif total_citations >= expected_density * 0.5:
        score += 5
        issues.append(f"Low citation density: {total_citations} refs in {word_count} words")
    else:
        issues.append(f"Very low citation density: {total_citations} refs in {word_count} words")

    # Cross-language citation usage for Chinese papers
    if normalize_language_code(getattr(ctx, 'language', '')) == 'zh' and getattr(ctx, 'citation_database', None):
        citation_map = {c.id: c for c in ctx.citation_database.citations}
        used_english = sum(
            1 for cite_id in set(citation_refs)
            if (getattr(citation_map.get(cite_id.strip('{}')), 'language', '') or '').lower() == 'english'
        )
        available_english = sum(
            1 for c in ctx.citation_database.citations
            if (getattr(c, 'language', '') or '').lower() == 'english'
        )
        if available_english > 0 and used_english == 0:
            issues.append("No English-language citations used in final draft despite English sources being available")
            score = max(0, score - 5)
    
    return score


def _score_completeness(ctx: 'DraftContext', issues: List[str]) -> int:
    """Score based on section completeness."""
    score = 0
    
    # Required sections (5 points each)
    sections = [
        ('Introduction', ctx.intro_output, 5),
        ('Literature Review', ctx.lit_review_output, 5),
        ('Methodology', ctx.methodology_output, 5),
        ('Results', ctx.results_output, 5),
        ('Conclusion', ctx.conclusion_output, 5),
    ]
    
    for name, content, points in sections:
        if content and len(content.strip()) > 100:
            score += points
        elif content and len(content.strip()) > 0:
            score += points // 2
            issues.append(f"{name} section too brief")
        else:
            issues.append(f"Missing {name} section")
    
    return score


def _score_structure(ctx: 'DraftContext', issues: List[str]) -> int:
    """Score based on markdown structure."""
    score = 0
    all_text = ctx.intro_output + ctx.body_output + ctx.conclusion_output
    
    # Has markdown headers (10 points)
    headers = re.findall(r'^#{1,3}\s+.+$', all_text, re.MULTILINE)
    if len(headers) >= 5:
        score += 10
    elif len(headers) >= 2:
        score += 5
        issues.append(f"Few section headers: {len(headers)}")
    else:
        issues.append("Missing section headers")
    
    # Has paragraphs (5 points)
    paragraphs = all_text.split('\n\n')
    paragraphs = [p for p in paragraphs if len(p.strip()) > 50]
    if len(paragraphs) >= 10:
        score += 5
    elif len(paragraphs) >= 5:
        score += 2
        issues.append(f"Few paragraphs: {len(paragraphs)}")
    else:
        issues.append("Very few paragraphs")
    
    # No obvious errors (10 points)
    error_patterns = [
        (r'\{cite_MISSING[^}]*\}', "Contains {cite_MISSING} placeholders"),
        (r'TODO', "Contains TODO markers"),
        (r'\[INSERT\]', "Contains [INSERT] placeholders"),
        (r'Lorem ipsum', "Contains Lorem ipsum placeholder text"),
    ]
    
    deductions = 0
    for pattern, message in error_patterns:
        if re.search(pattern, all_text, re.IGNORECASE):
            deductions += 2
            issues.append(message)
    
    score += max(0, 10 - deductions)
    
    return score


def run_quality_gate(ctx: 'DraftContext', strict: bool = False) -> QualityScore:
    """
    Run quality gate after compose phase.
    
    Args:
        ctx: DraftContext with compose outputs
        strict: If True, raise error on low quality. If False, log warning.
        
    Returns:
        QualityScore result
        
    Raises:
        ValueError: If strict=True and quality score < 50
    """
    logger.info("Running quality gate assessment...")
    
    result = score_draft_quality(ctx)
    
    logger.info(f"Quality Score: {result.total_score}/100")
    logger.info(f"  Word Count: {result.word_count_score}/25")
    logger.info(f"  Citations:  {result.citation_score}/25")
    logger.info(f"  Completeness: {result.completeness_score}/25")
    logger.info(f"  Structure:  {result.structure_score}/25")
    
    if result.issues:
        logger.info(f"Issues found: {len(result.issues)}")
        for issue in result.issues:
            logger.warning(f"  - {issue}")

    if result.hard_violations:
        logger.error(f"Hard integrity violations found: {len(result.hard_violations)}")
        for violation in result.hard_violations:
            logger.error(f"  - {violation}")
        raise ValueError(
            "Quality gate hard-failed due to integrity violations: "
            + "; ".join(result.hard_violations[:3])
        )
    
    if not result.passed:
        msg = f"Quality gate failed: score {result.total_score}/100 (minimum: 50)"
        if strict:
            raise ValueError(msg)
        else:
            logger.warning(msg)
            logger.warning("Continuing despite low quality score (strict=False)")
    else:
        logger.info(f"Quality gate passed: {result.total_score}/100")
    
    return result
