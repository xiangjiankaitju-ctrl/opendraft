#!/usr/bin/env python3
"""
ABOUTME: Revision module for OpenDraft V1
ABOUTME: Allows revising existing drafts with specific instructions using Generic LLM Provider
"""

import re
import logging
import time
import os
from pathlib import Path
from typing import Optional, Dict, Any

from config import get_config
from utils.export_professional import export_pdf, export_docx
from utils.llm_provider import create_llm_model

logger = logging.getLogger(__name__)


def _is_safe_file(path: Path, base_folder: Path) -> bool:
    """Check if file is safe (not a symlink escaping the folder)."""
    try:
        resolved = path.resolve()
        base_resolved = base_folder.resolve()
        return str(resolved).startswith(str(base_resolved))
    except (OSError, ValueError):
        return False


def find_draft_in_folder(folder: Path) -> Optional[Path]:
    """Find the main draft markdown file in an output folder."""
    folder = folder.resolve()
    exports_dir = folder / "exports"
    if exports_dir.exists():
        md_files = [f for f in exports_dir.glob("*.md") if _is_safe_file(f, folder)]
        export_files = [f for f in md_files if not any(
            x in f.name.lower() for x in ['intermediate', 'abstract', 'temp', '_generated']
        ) and not f.name.startswith(('_', '.')) and not f.is_symlink()]
        if export_files:
            for f in export_files:
                if 'final_draft' in f.name.lower():
                    return f
            return max(export_files, key=lambda p: p.stat().st_size)
    
    drafts_dir = folder / "drafts"
    if drafts_dir.exists():
        md_files = [f for f in drafts_dir.glob("*.md")
                    if _is_safe_file(f, folder) and not f.is_symlink()
                    and not f.name.startswith(('.', '_'))]
        if md_files:
            return max(md_files, key=lambda p: p.stat().st_size)

    for name in ["final_draft.md", "draft.md"]:
        candidate = folder / name
        if candidate.exists() and _is_safe_file(candidate, folder) and not candidate.is_symlink():
            return candidate

    md_files = [f for f in folder.glob("*.md")
                if _is_safe_file(f, folder) and not f.is_symlink()
                and not f.name.startswith(('.', '_'))]
    if md_files:
        return max(md_files, key=lambda p: p.stat().st_size)
    return None


def call_llm_revise(draft: str, instructions: str, model: str = None, max_retries: int = 3) -> str:
    """
    Call Generic LLM to revise a draft based on instructions.
    """
    config = get_config()
    config.validate_api_keys()
    
    # Use LLM_MODEL from env if not explicitly passed
    target_model = model or os.getenv('LLM_MODEL', 'gpt-4.1-nano')
    llm_model = create_llm_model(model_override=target_model)

    prompt = f"""You are an academic writing expert. Revise the following draft based on the user's instructions.

## REVISION INSTRUCTIONS
{instructions}

## IMPORTANT RULES
1. Return the COMPLETE revised draft, not just the changed parts
2. Maintain the same overall structure unless instructed otherwise
3. Preserve all citations ({{cite_XXX}} references and (Author, Year) citations)
4. Keep the academic tone and formatting
5. Do NOT add commentary or explanations - just return the revised draft

## CURRENT DRAFT
{draft}

## YOUR TASK
Return the complete revised draft below:
"""

    logger.info(f"Calling LLM ({target_model}) for revision...")

    last_error = None
    for attempt in range(max_retries):
        try:
            response = llm_model.generate_content(prompt)
            revised = response.text.strip()
            break
        except Exception as e:
            last_error = e
            wait_time = (attempt + 1) * 5
            logger.warning(f"LLM error, retrying in {wait_time}s: {e}")
            time.sleep(wait_time)
    else:
        raise last_error or Exception("Max retries exceeded")

    if revised.startswith("```markdown"):
        revised = revised[len("```markdown"):].strip()
    if revised.startswith("```"):
        revised = revised[3:].strip()
    if revised.endswith("```"):
        revised = revised[:-3].strip()

    return revised


def score_draft_simple(text: str) -> Dict[str, Any]:
    """Simplified quality scoring for revisions."""
    word_count = len(text.split())
    if word_count <= 100:
        word_score = int(word_count * 0.05)
    elif word_count <= 500:
        word_score = 5 + int((word_count - 100) / 400 * 10)
    elif word_count <= 1500:
        word_score = 15 + int((word_count - 500) / 1000 * 10)
    else:
        word_score = min(30, 25 + int((word_count - 1500) / 1500 * 5))

    cite_format = len(re.findall(r'\{cite_\d+\}', text))
    parenthetical = len(re.findall(r'\([A-Z][a-z]+(?:\s+(?:&|and)\s+[A-Z][a-z]+|\s+et\s+al\.? )?,?\s*\d{4}\)', text))
    citations = cite_format + parenthetical
    citation_score = min(25, int((citations / 15) * 25))

    headers = len(re.findall(r'^#{1,3}\s+.+$', text, re.MULTILINE))
    structure_score = min(25, int((headers / 6) * 25))

    sections_found = 0
    for keyword in ['introduction', 'literature', 'methodology', 'results', 'conclusion', 'discussion']:
        if keyword.lower() in text.lower():
            sections_found += 1
    completeness_score = min(20, sections_found * 4)

    score = word_score + citation_score + structure_score + completeness_score
    return {'overall_score': min(100, score), 'word_count': word_count, 'citations': citations}


def revise_draft(target: Path, instructions: str, version_suffix: str = None, model: str = None) -> Dict[str, Any]:
    """Revise an existing draft using Generic LLM."""
    if target.is_file():
        draft_path = target
        output_dir = target.parent
    elif target.is_dir():
        draft_path = find_draft_in_folder(target)
        if not draft_path: raise FileNotFoundError(f"No draft found in {target}")
        output_dir = target / "exports" if (target / "exports").exists() else target
    else:
        raise FileNotFoundError(f"Target not found: {target}")

    draft_text = draft_path.read_text(encoding='utf-8')
    score_before = score_draft_simple(draft_text)
    
    revised_text = call_llm_revise(draft_text, instructions, model=model)
    score_after = score_draft_simple(revised_text)

    base_name = re.sub(r'_v\d+$', '', draft_path.stem)
    if version_suffix is None:
        v = 2
        while (output_dir / f"{base_name}_v{v}.md").exists(): v += 1
        version_suffix = f"v{v}"

    md_path = output_dir / f"{base_name}_{version_suffix}.md"
    pdf_path = output_dir / f"{base_name}_{version_suffix}.pdf"
    docx_path = output_dir / f"{base_name}_{version_suffix}.docx"

    md_path.write_text(revised_text, encoding='utf-8')
    export_pdf(md_path, pdf_path)
    export_docx(md_path, docx_path)

    return {
        'md_path': md_path, 'pdf_path': pdf_path, 'docx_path': docx_path,
        'score_before': score_before['overall_score'], 'score_after': score_after['overall_score'],
        'delta': score_after['overall_score'] - score_before['overall_score'],
        'word_count': len(revised_text.split()), 'word_count_before': score_before['word_count']
    }