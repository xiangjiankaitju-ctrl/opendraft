#!/usr/bin/env python3
"""
Abstract Generator Utility - Production-Grade Implementation

SOLID Principles:
- Single Responsibility: Only handles abstract generation
- Open/Closed: Extensible for new languages without modification
- Interface Segregation: Clean function interface
- Dependency Inversion: Depends on abstractions (model interface)

DRY Principle:
- Reusable by all draft generation scripts
- Centralized logic for abstract generation and replacement
"""

import re
import logging
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _measure_abstract_length(text: str, language: str) -> int:
    """Measure abstract length with language-aware rules.

    - Chinese: primarily CJK character count (with fallback to token count)
    - Others: whitespace token count
    """
    text = (text or "").strip()
    if not text:
        return 0

    lang = (language or "english").lower()
    if lang == "chinese":
        cjk_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        tokens = len(text.split())
        return max(cjk_chars, tokens)

    return len(text.split())


def _is_abstract_length_valid(length: int, language: str) -> bool:
    """Language-aware abstract guardrails.

    Chinese generation frequently mixes punctuation/English tokens, so using
    CJK-aware length thresholds avoids false negatives from whitespace counting.
    """
    lang = (language or "english").lower()
    if lang == "chinese":
        return 180 <= length <= 900
    return 200 <= length <= 350

def detect_draft_language(draft_content: str) -> str:
    """
    Detect draft language from content.

    Args:
        draft_content: Full draft markdown content

    Returns:
        Language code: 'english', 'german', etc.
    """
    # Check for German indicators
    german_indicators = [
        '## Zusammenfassung',
        '## Inhaltsverzeichnis',
        '## Einleitung',
        '## Fazit',
        'Schlüsselwörter:'
    ]

    if any(indicator in draft_content for indicator in german_indicators):
        return 'german'

    chinese_indicators = [
        '## 摘要',
        '## 目录',
        '## 引言',
        '## 结论',
        '关键词：',
    ]
    if any(indicator in draft_content for indicator in chinese_indicators):
        return 'chinese'

    # Default to English
    return 'english'


def has_placeholder_abstract(draft_content: str) -> bool:
    """
    Check if draft has a placeholder abstract that needs generation.

    Args:
        draft_content: Full draft markdown content

    Returns:
        True if placeholder found, False if real abstract exists
    """
    placeholders = [
        '[Abstract will be generated',
        '[Zusammenfassung wird während der PDF-Generierung',
        '[Zusammenfassung wird automatisch',
        '[摘要将在',
    ]

    return any(placeholder in draft_content for placeholder in placeholders)


def extract_draft_for_abstract(draft_content: str, max_chars: int = 15000) -> str:
    """
    Extract relevant content for abstract generation (introduction + conclusion).

    Args:
        draft_content: Full draft markdown content
        max_chars: Maximum characters to extract

    Returns:
        Truncated draft content for context
    """
    # Skip frontmatter
    content_start = 0
    if draft_content.startswith('---'):
        end_frontmatter = draft_content.find('---', 3)
        if end_frontmatter != -1:
            content_start = end_frontmatter + 3

    # Skip TOC and abstract sections
    toc_match = re.search(r'## (Table of Contents|Inhaltsverzeichnis|目录)', draft_content[content_start:])
    if toc_match:
        content_start += toc_match.end()

    abstract_match = re.search(r'## (Abstract|Zusammenfassung|摘要)', draft_content[content_start:])
    if abstract_match:
        abstract_start = content_start + abstract_match.start()
        newpage_match = re.search(r'\\newpage', draft_content[abstract_start:])
        if newpage_match:
            content_start = abstract_start + newpage_match.end()

    # Get introduction (first 7500 chars of actual content)
    main_content = draft_content[content_start:].strip()
    introduction = main_content[:7500]

    # Try to find conclusion
    conclusion = ""
    conc_patterns = [
        r'# (Conclusion|Fazit|Schlussfolgerung)\n+(.*?)(?=\n---|$)',
        r'## (Conclusion|Fazit|Schlussfolgerung)\n+(.*?)(?=\n---|$)'
    ]

    for pattern in conc_patterns:
        conc_match = re.search(pattern, draft_content, re.DOTALL)
        if conc_match:
            conclusion = conc_match.group(2).strip()[:7500]
            break

    if not conclusion:
        # Fall back to last 7500 chars before references
        refs_pattern = r'\n---\n+\d+\.'
        refs_match = re.search(refs_pattern, draft_content)
        if refs_match:
            conclusion = draft_content[max(0, refs_match.start() - 7500):refs_match.start()].strip()
        else:
            conclusion = draft_content[-7500:].strip()

    # Combine introduction and conclusion
    context = f"{introduction}\n\n...\n\n{conclusion}"

    # Truncate to max_chars if needed
    if len(context) > max_chars:
        context = context[:max_chars] + "..."

    return context


def replace_placeholder_with_abstract(draft_content: str, generated_abstract: str, language: str = 'english') -> str:
    """
    Replace placeholder abstract with generated content.

    Args:
        draft_content: Full draft markdown content
        generated_abstract: Generated abstract text (without header)
        language: Draft language

    Returns:
        Updated draft content with real abstract
    """
    # Clean up the generated abstract (remove any meta-comments)
    generated_abstract = re.sub(
        r'^(Here is the abstract|Hier ist die Zusammenfassung).*?\n+',
        '',
        generated_abstract,
        flags=re.IGNORECASE
    ).strip()

    # Define placeholder patterns (handle optional leading whitespace from indented templates)
    if language == 'german':
        placeholder_pattern = r'^\s*## Zusammenfassung\n+\s*\[Zusammenfassung wird.*?\]\n+\s*\\\\?newpage'
        replacement = f"## Zusammenfassung\n\n{generated_abstract}\n\n\\\\newpage"
    elif language == 'chinese':
        placeholder_pattern = r'^\s*## 摘要\n+\s*\[摘要将.*?\]\n*(?:---?\n*|\s*\\\\?newpage)?'
        replacement = f"## 摘要\n\n{generated_abstract}\n\n\\\\newpage"
    else:
        # Match abstract placeholder with optional whitespace, brackets, and newpage
        placeholder_pattern = r'^\s*## Abstract\n+\s*\[Abstract will be generated.*?\]\n*(?:---?\n*|\s*\\\\?newpage)?'
        replacement = f"## Abstract\n\n{generated_abstract}\n\n\\\\newpage"

    # Replace placeholder (MULTILINE to match ^ at line start, DOTALL to match . across lines)
    updated_content = re.sub(placeholder_pattern, replacement, draft_content, flags=re.DOTALL | re.MULTILINE)

    # Verify replacement happened
    if updated_content == draft_content:
        logger.warning("Placeholder pattern not found - trying alternative patterns")

        # Try alternative patterns (account for optional leading whitespace from indented templates)
        alt_patterns = [
            # Match with \newpage (escaped in markdown as \\newpage) - with optional whitespace
            (r'^\s*## Abstract\n+\s*\[.*?\]\n+\s*\\\\newpage', f"## Abstract\n\n{generated_abstract}\n\n\\\\newpage"),
            (r'^\s*## Zusammenfassung\n+\s*\[.*?\]\n+\s*\\\\newpage', f"## Zusammenfassung\n\n{generated_abstract}\n\n\\\\newpage"),
            # Match with literal \newpage - with optional whitespace
            (r'^\s*## Abstract\n+\s*\[.*?\]\n+\s*\\newpage', f"## Abstract\n\n{generated_abstract}\n\n\\newpage"),
            (r'^\s*## Zusammenfassung\n+\s*\[.*?\]\n+\s*\\newpage', f"## Zusammenfassung\n\n{generated_abstract}\n\n\\newpage"),
            # Match without newpage - with optional whitespace
            (r'^\s*## Abstract\n+\s*\[.*?\]', f"## Abstract\n\n{generated_abstract}"),
            (r'^\s*## Zusammenfassung\n+\s*\[.*?\]', f"## Zusammenfassung\n\n{generated_abstract}"),
            (r'^\s*## 摘要\n+\s*\[.*?\]\n+\s*\\\\newpage', f"## 摘要\n\n{generated_abstract}\n\n\\\\newpage"),
            (r'^\s*## 摘要\n+\s*\[.*?\]\n+\s*\\newpage', f"## 摘要\n\n{generated_abstract}\n\n\\newpage"),
            (r'^\s*## 摘要\n+\s*\[.*?\]', f"## 摘要\n\n{generated_abstract}"),
        ]

        for pattern, repl in alt_patterns:
            updated_content = re.sub(pattern, repl, draft_content, flags=re.DOTALL | re.MULTILINE)
            if updated_content != draft_content:
                logger.info("Alternative pattern matched successfully")
                break

    return updated_content


def generate_abstract_for_draft(
    draft_path: Path,
    model,
    run_agent_func,
    output_dir: Path,
    target_language: Optional[str] = None,
    verbose: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    Generate and integrate abstract for a draft.

    This is the main entry point for abstract generation. It:
    1. Reads the draft
    2. Checks if abstract generation is needed
    3. Calls the Abstract Generator agent
    4. Replaces the placeholder with generated content
    5. Saves the updated draft

    Args:
        draft_path: Path to draft markdown file
        model: LLM model instance
        run_agent_func: Function to run agent (from test_utils)
        output_dir: Output directory for intermediate files
        verbose: Print progress messages

    Returns:
        Tuple of (success: bool, updated_content: str or None)
    """
    # Read draft
    with open(draft_path, 'r', encoding='utf-8') as f:
        draft_content = f.read()

    # Detect language (but allow pipeline context to override detection)
    language = detect_draft_language(draft_content)
    if target_language:
        lang_map = {
            'zh': 'chinese',
            'zh-cn': 'chinese',
            'zh-tw': 'chinese',
            'en': 'english',
            'de': 'german',
        }
        language = lang_map.get(target_language.lower(), language)

    # Check if abstract generation is needed
    if not has_placeholder_abstract(draft_content):
        if verbose:
            print("✅ Draft already has a full abstract - skipping generation")
        return True, draft_content

    if verbose:
        print(f"📝 Placeholder abstract detected ({language}) - generating full abstract...")

    # Extract context for abstract generation
    draft_context = extract_draft_for_abstract(draft_content)

    if verbose:
        print(f"  • Extracted {len(draft_context)} chars of context")
        print(f"  • Language: {language}")

    # Prepare user input for Abstract Generator agent
    user_input = f"""Generate an academic abstract for this draft.

**Language:** {language.title()}

**Draft Context:**
{draft_context}

**Instructions:**
- Generate a 4-paragraph abstract (250-300 words)
- Include 12-15 relevant keywords
- Follow standard academic abstract structure
- Output ONLY the abstract content (no meta-comments)
"""

    # Call Abstract Generator agent
    try:
        generated_abstract = ""
        best_candidate = ""
        best_score = float('inf')
        for attempt in range(3):
            extra_constraint = ""
            if attempt > 0:
                extra_constraint = "\n- Previous attempt was outside the required length. You MUST produce 250-300 words and 4 academic paragraphs."
            generated_abstract = run_agent_func(
                model=model,
                name="Abstract Generator (Agent #6.5)",
                prompt_path="prompts/06_enhance/abstract_generator.md",
                user_input=user_input + extra_constraint,
                save_to=output_dir / f"16_abstract_generated_{attempt + 1}.md"
            )

            if not generated_abstract:
                continue

            measured_len = _measure_abstract_length(generated_abstract, language)
            if _is_abstract_length_valid(measured_len, language):
                break

            # Keep best candidate as safe fallback to avoid leaving placeholders.
            target_mid = 300 if language == 'chinese' else 275
            score = abs(measured_len - target_mid)
            if score < best_score:
                best_score = score
                best_candidate = generated_abstract

        if not generated_abstract and best_candidate:
            generated_abstract = best_candidate

        if not generated_abstract:
            if verbose:
                print("❌ Abstract generation failed - agent returned no content")
            return False, None

        measured_len = _measure_abstract_length(generated_abstract, language)
        metric_label = "chars" if language == "chinese" else "words"
        if verbose:
            print(f"✅ Abstract generated: {measured_len} {metric_label}")

        # If retries still miss strict guard, keep best candidate rather than
        # leaving placeholder content in final draft.
        if not _is_abstract_length_valid(measured_len, language):
            if verbose:
                print(f"⚠️ Abstract length outside preferred range after retries - using best available candidate")

        # Replace placeholder with generated abstract
        updated_content = replace_placeholder_with_abstract(draft_content, generated_abstract, language)

        if updated_content == draft_content:
            if verbose:
                print("❌ ERROR: Failed to replace placeholder abstract")
            return False, None

        # Save updated draft
        with open(draft_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)

        if verbose:
            print(f"✅ Abstract integrated into draft at {draft_path}")

        return True, updated_content

    except Exception as e:
        if verbose:
            print(f"❌ ERROR generating abstract: {e}")
        return False, None


