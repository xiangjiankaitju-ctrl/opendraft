#!/usr/bin/env python3
"""
ABOUTME: Compile and export phase — assembly, abstract, PDF/DOCX export
ABOUTME: Also handles expose mode early-exit export
"""

import re
import time
import logging
import zipfile
import os
import json
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime

from .context import DraftContext

logger = logging.getLogger(__name__)


def _extract_citation_ids(text: str) -> set:
    """Extract {cite_XXX} ids from text."""
    return {m.strip('{}') for m in re.findall(r'\{cite_\d{3}\}', text or '')}


def run_expose_export(ctx: DraftContext) -> Tuple[Path, Path]:
    """
    Handle expose mode: generate research overview + outline only.

    Returns: (pdf_path, docx_path)
    """
    from utils.export_professional import export_pdf, export_docx
    from utils.text_utils import slugify, get_language_name

    logger.info("=" * 80)
    logger.info("EXPOSE MODE: Generating research overview (skipping full draft)")
    logger.info("=" * 80)

    if ctx.tracker:
        ctx.tracker.log_activity("📋 Creating Research Expose...", event_type="milestone", phase="exporting")
        ctx.tracker.update_phase("exporting", progress_percent=70, details={"stage": "creating_expose"})

    language_name = get_language_name(ctx.language)

    if ctx.verbose:
        print("\n📋 EXPOSE MODE: Creating research overview...")

    # Extract research credibility info from citations
    journals = set()
    years = set()
    author_teams = []
    all_authors = set()
    source_types = {"journal": 0, "book": 0, "conference": 0, "other": 0}
    top_tier_journals = {"Nature", "Science", "Cell", "PNAS", "JAMA", "Lancet", "BMJ",
                         "IEEE", "ACM", "Physical Review", "Chemical Reviews"}

    top_tier_count = 0
    recent_count = 0  # Last 5 years
    current_year = datetime.now().year

    for citation in ctx.citation_database.citations:
        if citation.journal:
            journals.add(citation.journal)
            # Check if top-tier
            if any(top in citation.journal for top in top_tier_journals):
                top_tier_count += 1
        if citation.year:
            years.add(citation.year)
            if current_year - citation.year <= 5:
                recent_count += 1
        if citation.authors:
            all_authors.update(citation.authors)
            lead = citation.authors[0] if citation.authors else "Unknown"
            if len(citation.authors) > 1:
                author_teams.append(f"{lead} et al.")
            else:
                author_teams.append(lead)
        # Count source types
        src_type = getattr(citation, 'source_type', 'journal')
        if src_type in source_types:
            source_types[src_type] += 1
        else:
            source_types["other"] += 1

    total_sources = len(ctx.citation_database.citations)
    year_range = f"{min(years)}-{max(years)}" if years else "Various"
    top_journals = ", ".join(list(journals)[:5]) if journals else "Multiple sources"
    key_researchers = ", ".join(author_teams[:5]) if author_teams else "Multiple researchers"
    recency_pct = int((recent_count / total_sources) * 100) if total_sources else 0
    unique_authors = len(all_authors)

    # Compile the expose document
    expose_content = f"""# Research Expose: {ctx.topic}

**Generated:** {datetime.now().strftime('%Y-%m-%d')}
**Academic Level:** {ctx.academic_level.title()}
**Language:** {language_name}

---

## Executive Summary

This research expose provides a preliminary overview of the topic "{ctx.topic}" based on an analysis of {len(ctx.citation_database.citations)} academic sources. It includes a structured outline for a potential full research paper and a comprehensive bibliography.

---

## Research Sources Overview

| Metric | Value |
|--------|-------|
| **Total Sources** | {total_sources} peer-reviewed papers |
| **Publication Years** | {year_range} |
| **Recent Sources** | {recency_pct}% from last 5 years |
| **Unique Authors** | {unique_authors} researchers |
| **Top-Tier Journals** | {top_tier_count} sources |
| **Key Journals** | {top_journals} |
| **Key Research Teams** | {key_researchers} |

This expose synthesizes findings from {unique_authors} researchers across {len(journals)} journals. {recency_pct}% of sources are from the last 5 years, indicating current research relevance.

---

## Research Outline

{ctx.formatter_output}

---

## Key Research Findings

{ctx.scribe_output[:4000] if len(ctx.scribe_output) > 4000 else ctx.scribe_output}

---

## Identified Research Gaps

{ctx.signal_output[:2000] if len(ctx.signal_output) > 2000 else ctx.signal_output}

---

## Bibliography

"""
    for citation in ctx.citation_database.citations:
        authors_str = ", ".join(citation.authors[:3])
        if len(citation.authors) > 3:
            authors_str += " et al."
        expose_content += f"- {authors_str} ({citation.year}). {citation.title}"
        if citation.journal:
            expose_content += f". *{citation.journal}*"
        if citation.doi:
            expose_content += f". https://doi.org/{citation.doi}"
        expose_content += "\n\n"

    expose_content += f"""
---

## Next Steps

This research expose serves as a starting point for a comprehensive {ctx.academic_level}-level paper. To develop this into a full draft:

1. **Expand the outline** into detailed chapter content
2. **Conduct deeper analysis** of the identified sources
3. **Address the research gaps** highlighted above
4. **Develop original arguments** based on the literature review

---

*This expose was generated as a research overview. It is intended as a planning tool and starting point for further development.*
"""

    # Save expose markdown
    expose_md_path = ctx.folders['drafts'] / "00_expose.md"
    expose_md_path.write_text(expose_content, encoding='utf-8')
    logger.info(f"Expose markdown saved: {expose_md_path}")

    if ctx.tracker:
        ctx.tracker.log_activity("📄 Exporting Research Expose...", event_type="info", phase="exporting")
        ctx.tracker.update_phase("exporting", progress_percent=85, details={"stage": "exporting_expose"})

    # Export as PDF and DOCX
    topic_slug = slugify(ctx.topic, max_length=50)
    if not topic_slug:
        topic_slug = "research_expose"

    if ctx.verbose:
        print("📄 Exporting PDF...")

    pdf_path = ctx.folders['exports'] / f"{topic_slug}_expose.pdf"
    # Try 'auto' engine which falls back through multiple engines
    # For expose (quick overview), PDF is optional - don't fail if it doesn't work
    pdf_success = export_pdf(md_file=expose_md_path, output_pdf=pdf_path, engine='auto')

    if not pdf_success or not pdf_path.exists():
        logger.warning(f"PDF export failed for expose - continuing with DOCX only")
        if ctx.verbose:
            print("   ⚠️ PDF export failed (continuing with DOCX)")
        pdf_path = None  # Signal that PDF wasn't created

    if ctx.verbose:
        print("📝 Exporting Word document...")

    docx_path = ctx.folders['exports'] / f"{topic_slug}_expose.docx"
    docx_success = export_docx(md_file=expose_md_path, output_docx=docx_path)

    if not docx_success or not docx_path.exists():
        raise RuntimeError(f"DOCX export failed for expose: {docx_path}")

    if ctx.tracker:
        ctx.tracker.log_activity("🎉 Research Expose complete!", event_type="milestone", phase="completed")
        ctx.tracker.update_phase(
            "exporting",
            progress_percent=100,
            sources_count=len(ctx.citation_database.citations),
            chapters_count=1,
            details={"stage": "expose_complete", "milestone": "expose_complete"},
        )

    if ctx.verbose:
        print(f"\n\u2705 Research Expose complete!")
        if pdf_path:
            print(f"   PDF: {pdf_path}")
        print(f"   DOCX: {docx_path}")

    # Record artifact metadata for final reporting.
    ctx.export_artifacts = {
        "pdf_generated": bool(pdf_path and pdf_path.suffix.lower() == '.pdf' and pdf_path.exists()),
        "pdf_path": str(pdf_path) if pdf_path else None,
        "markdown_fallback_path": str(expose_md_path),
        "docx_path": str(docx_path),
    }

    # Return paths (pdf_path may be None if PDF export failed, fall back to md)
    return pdf_path or expose_md_path, docx_path


def run_compile_and_export(ctx: DraftContext) -> Tuple[Path, Path]:
    """
    Execute the compile and export phase: assemble draft, generate abstract, export.

    Returns: (pdf_path, docx_path)
    """
    from utils.agent_runner import run_agent
    from utils.citation_compiler import CitationCompiler
    from utils.abstract_generator import generate_abstract_for_draft
    from utils.export_professional import export_pdf, export_docx
    from utils.text_utils import clean_ai_language, strip_meta_text, localize_chapter_headings, clean_agent_output
    from utils.document_ast import normalize_document_markdown, normalize_language_code
    from utils.docx_export_pipeline import clean_language_residuals
    from utils.text_cleanup import apply_full_cleanup
    from utils.text_utils import slugify

    ctx.language = normalize_language_code(ctx.language)
    is_zh = ctx.language == "zh"
    format_report: dict[str, object] = {"fatal": False, "warnings": [], "auto_fixed": []}

    if ctx.verbose:
        print("\n🔧 PHASE 4: COMPILE")

    if ctx.tracker:
        ctx.tracker.log_activity("🔧 Starting document compilation", event_type="milestone", phase="compiling")
        ctx.tracker.update_phase("compiling", progress_percent=75, details={"stage": "assembling_draft"})
        ctx.tracker.check_cancellation()

    # Strip only duplicate wrapper headings from section outputs. In stable
    # compile mode, the merged 02_main_body.md is the sole body source.
    intro_clean, body_clean, conclusion_clean = _select_compile_section_texts(ctx, clean_agent_output)
    body_clean = _remove_compile_artifact_paragraphs(body_clean)

    appendices_file = ctx.folders['drafts'] / "04_appendices.md"
    if appendices_file.exists():
        appendix_content = appendices_file.read_text(encoding='utf-8')
        appendix_clean = _strip_first_header(clean_agent_output(appendix_content))
    else:
        appendix_clean = ""

    current_date = datetime.now().strftime("%B %Y")

    # Calculate word count / pages
    draft_text = f"{intro_clean}\n{body_clean}\n{conclusion_clean}\n{appendix_clean}"
    word_count = len(draft_text.split())
    pages_estimate = word_count // 250

    # Labels
    draft_type_labels = {
        'research_paper': '研究论文' if is_zh else 'Research Paper',
        'bachelor': '本科论文草稿' if is_zh else 'Bachelor Draft',
        'master': '硕士论文草稿' if is_zh else 'Master Draft',
        'phd': '博士论文草稿' if is_zh else 'PhD Dissertation',
    }
    draft_type = draft_type_labels.get(ctx.academic_level, '硕士论文草稿' if is_zh else 'Master Draft')

    degree_labels = {
        'research_paper': '研究论文' if is_zh else 'Research Paper',
        'bachelor': 'Bachelor of Science',
        'master': 'Master of Science',
        'phd': 'Doctor of Philosophy',
    }
    degree = degree_labels.get(ctx.academic_level, 'Master of Science')

    # YAML metadata
    yaml_author = ctx.author_name or "OpenDraft AI"
    yaml_institution = ctx.institution or "OpenDraft University"
    yaml_department = ctx.department or "Department of Computer Science"
    yaml_faculty = ctx.faculty or "Faculty of Engineering"
    yaml_advisor = ctx.advisor or "Prof. Dr. OpenDraft Supervisor"
    yaml_second_examiner = ctx.second_examiner or "Prof. Dr. Second Examiner"
    yaml_location = ctx.location or "Munich"
    yaml_student_id = ctx.student_id or "N/A"

    language = ctx.language
    yaml_word_count = f"{word_count:,} 字/词" if is_zh else f"{word_count:,} words"
    full_draft = f"""---
title: "{ctx.topic}"
author: "{yaml_author}"
date: "{current_date}"
language: "{language}"
institution: "{yaml_institution}"
department: "{yaml_department}"
faculty: "{yaml_faculty}"
degree: "{degree}"
advisor: "{yaml_advisor}"
second_examiner: "{yaml_second_examiner}"
location: "{yaml_location}"
student_id: "{yaml_student_id}"
project_type: "{draft_type}"
word_count: "{yaml_word_count}"
pages: "{pages_estimate}"
---

{_assemble_markdown_body(ctx, intro_clean, body_clean, conclusion_clean, appendix_clean)}
"""
    _write_heading_debug_snapshot(ctx, "after_full_draft_assembled_headings.json", full_draft, "after_full_draft_assembled")
    _record_format_stage_diagnostics(format_report, full_draft, ctx.language, "after_full_draft_assembled")

    # Citation compilation
    if ctx.tracker:
        ctx.tracker.log_activity("📚 Compiling citations and references...", event_type="info", phase="compiling")

    compiler = CitationCompiler(database=ctx.citation_database, model=ctx.model)

    # Track citation usage before replacement so quality/reporting can compare
    # available citations vs citations actually used in draft text.
    used_citation_ids = _extract_citation_ids(full_draft)
    ctx.citation_usage_metrics = {
        "used_unique_citations": len(used_citation_ids),
        "used_citation_ids": sorted(used_citation_ids),
    }

    if ctx.citation_metrics is None:
        ctx.citation_metrics = {}
    ctx.citation_metrics["used_unique_citations"] = len(used_citation_ids)

    reference_list = compiler.generate_reference_list(full_draft)
    compiled_draft, replaced_ids, failed_ids = compiler.compile_citations(full_draft, research_missing=True, verbose=ctx.verbose)

    if ctx.tracker:
        ctx.tracker.log_activity(f"\u2705 Citations compiled ({len(replaced_ids)} references)", event_type="found", phase="compiling")

    # Remove template References section and append generated one
    compiled_draft = re.sub(
        r'^\s*#+ (?:\d+\.\s*)?(?:References|Bibliography|参考文献)\s*\n\s*\[Citations will be compiled\]\s*',
        '',
        compiled_draft,
        flags=re.MULTILINE,
    )
    compiled_draft = compiled_draft + _localize_reference_list_heading(reference_list, ctx.language)
    compiled_draft = _remove_compile_artifact_paragraphs(compiled_draft)
    compiled_draft = _normalize_markdown_page_breaks(compiled_draft, output="comment")

    # Save intermediate draft for abstract generation
    intermediate_md_path = ctx.folders['exports'] / "INTERMEDIATE_DRAFT.md"
    intermediate_md_path.write_text(compiled_draft, encoding='utf-8')

    # Generate abstract
    if ctx.tracker:
        ctx.tracker.log_activity("📝 Generating abstract...", event_type="info", phase="compiling")

    abstract_success, abstract_updated_content = generate_abstract_for_draft(
        draft_path=intermediate_md_path,
        model=ctx.model,
        run_agent_func=run_agent,
        output_dir=ctx.folders['exports'],
        target_language=ctx.language,
        verbose=ctx.verbose,
    )

    if ctx.tracker:
        ctx.tracker.log_activity("\u2705 Abstract generated", event_type="found", phase="compiling")

    final_draft = abstract_updated_content if abstract_success and abstract_updated_content else compiled_draft
    _write_heading_debug_snapshot(ctx, "after_abstract_integrated_headings.json", final_draft, "after_abstract_integrated")
    _record_format_stage_diagnostics(format_report, final_draft, ctx.language, "after_abstract_integrated")

    # Generate filename
    base_filename = slugify(ctx.topic, max_length=50)
    if not base_filename:
        base_filename = "research_paper"

    # Clean and save final markdown
    final_md_path = ctx.folders['exports'] / f"{base_filename}.md"
    if _keep_docx_debug_artifacts():
        (ctx.folders['exports'] / "final_before_cleanup.md").write_text(final_draft, encoding="utf-8")

    final_draft = fix_single_line_tables(final_draft)
    final_draft = deduplicate_appendices(final_draft)
    final_draft = clean_malformed_markdown(final_draft)
    final_draft = clean_agent_output(final_draft)
    final_draft = _normalize_markdown_page_breaks(final_draft, output="comment")
    final_draft = _remove_compile_artifact_paragraphs(final_draft)

    # Apply comprehensive text cleanup only to ordinary paragraph blocks. Tables,
    # headings, references, URLs/DOIs, code, captions, and page breaks are
    # protected because global prose cleanup can corrupt document structure.
    cleanup_result = _apply_structure_aware_final_cleanup(
        final_draft,
        language=ctx.language,
        paragraph_cleanup_func=apply_full_cleanup,
        paragraph_ai_cleanup_func=clean_ai_language,
    )
    final_draft = cleanup_result["text"]
    cleanup_stats = cleanup_result["stats"]
    for key in ("fillers", "vocab_diversified", "claims_calibrated"):
        cleanup_stats.setdefault(key, 0)
    total_fixes = sum(cleanup_stats.values())
    logger.info(f"Text cleanup applied: {cleanup_stats}")

    if ctx.verbose and total_fixes > 0:
        print(f"   ✨ Text cleanup: {total_fixes} fixes (fillers={cleanup_stats['fillers']}, "
              f"vocab={cleanup_stats['vocab_diversified']}, claims={cleanup_stats['claims_calibrated']})")

    if ctx.tracker and total_fixes > 0:
        ctx.tracker.log_activity(
            f"✨ Prose polished ({total_fixes} fixes)",
            event_type="info",
            phase="compiling"
        )

    final_draft = strip_meta_text(final_draft)
    final_draft = localize_chapter_headings(final_draft, ctx.language)
    final_draft = _normalize_formal_academic_headings(final_draft, ctx.language)
    final_draft = _ensure_required_academic_top_headings(final_draft, ctx.language)
    final_draft, structure_doc = normalize_document_markdown(final_draft, ctx.language)
    for warning in structure_doc.warnings:
        _add_format_warning(
            format_report,
            warning.get("type", "document_structure"),
            warning.get("message", str(warning)),
            warning.get("action", "Recorded during AST structure normalization."),
        )
    final_draft = _remove_forbidden_cover_metadata(final_draft)
    final_draft = _normalize_residual_citation_tokens(final_draft, ctx.language)
    if ctx.language == "zh":
        final_draft = _localize_chinese_abstract_labels(final_draft)
        final_draft = _normalize_chinese_body_outline(final_draft)
        final_draft = _clean_chinese_final_artifacts(final_draft)
    final_draft = _normalize_yaml_language(final_draft, ctx.language)
    final_draft = _normalize_doi_url_case(final_draft)
    final_draft = _normalize_markdown_page_breaks(final_draft, output="comment")
    final_draft = collapse_duplicate_pagebreaks(final_draft)
    final_draft, repair_report = finalize_or_repair_markdown(final_draft, ctx.language)
    final_draft, residual_fixes = clean_language_residuals(final_draft, ctx.language)
    if residual_fixes:
        format_report.setdefault("language_cleanup", {})["residuals_fixed"] = residual_fixes
    _merge_format_report(format_report, repair_report)
    manifest = _build_document_structure_manifest(final_draft, ctx.language)
    _write_document_structure_manifest(ctx, manifest)
    _write_heading_debug_snapshot(ctx, "after_final_cleanup_headings.json", final_draft, "after_final_cleanup")
    _record_format_stage_diagnostics(format_report, final_draft, ctx.language, "after_final_cleanup")
    _handle_format_validation_result(format_report, ctx.verbose)
    _assert_markdown_table_rows_not_reduced(compiled_draft, final_draft)

    if _keep_docx_debug_artifacts():
        (ctx.folders['exports'] / "final_after_cleanup.md").write_text(final_draft, encoding="utf-8")

    final_md_path.write_text(final_draft, encoding='utf-8')
    _write_format_warnings_report(ctx, format_report)

    if ctx.verbose:
        print(f"\u2705 Draft compiled: {len(final_draft):,} characters")

    # ====================================================================
    # EXPORT
    # ====================================================================
    if ctx.verbose:
        print("\n📄 PHASE 5: EXPORT")

    if ctx.tracker:
        ctx.tracker.log_activity("📄 Starting document export", event_type="milestone", phase="exporting")
        ctx.tracker.update_exporting(export_type="PDF and DOCX")
        ctx.tracker.check_cancellation()

    # PDF export
    requested_pdf_path = ctx.folders['exports'] / f"{base_filename}.pdf"

    if ctx.tracker:
        ctx.tracker.log_activity("📑 Generating professional PDF document...", event_type="info", phase="exporting")

    if ctx.verbose:
        print("📄 Exporting PDF (professional formatting)...")

    # Use auto mode to allow engine fallback when Pandoc/LaTeX is unavailable.
    # PDF is best-effort in full mode; DOCX generation must still continue.
    pdf_success = export_pdf(md_file=final_md_path, output_pdf=requested_pdf_path, engine='auto')
    pdf_generated = bool(pdf_success and requested_pdf_path.exists())

    if not pdf_generated:
        logger.warning("PDF export failed - continuing with DOCX and markdown outputs")
        if ctx.verbose:
            print("   ⚠️ PDF export failed (continuing with DOCX)")
        if ctx.tracker:
            ctx.tracker.log_activity("⚠️ PDF export unavailable; continuing with DOCX", event_type="warning", phase="exporting")
    elif ctx.tracker:
        ctx.tracker.log_activity("\u2705 PDF document ready", event_type="found", phase="exporting")

    if ctx.tracker:
        ctx.tracker.log_activity("📝 Creating Word document...", event_type="info", phase="exporting")

    # DOCX export
    docx_path = ctx.folders['exports'] / f"{base_filename}.docx"
    docx_success = export_docx(md_file=final_md_path, output_docx=docx_path)

    if not docx_success or not docx_path.exists():
        raise RuntimeError(f"DOCX export failed - file not created: {docx_path}")

    if ctx.tracker:
        ctx.tracker.log_activity("\u2705 Word document ready", event_type="found", phase="exporting")

    # ZIP bundle
    zip_path = ctx.folders['exports'] / f"{base_filename}.zip"
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            if pdf_generated:
                zf.write(requested_pdf_path, requested_pdf_path.name)
            zf.write(docx_path, docx_path.name)
            zf.write(final_md_path, final_md_path.name)
        if ctx.tracker:
            ctx.tracker.log_activity("📦 ZIP bundle created", event_type="found", phase="exporting")
    except Exception as zip_error:
        logger.warning(f"ZIP creation failed (non-critical): {zip_error}")

    if ctx.tracker:
        ctx.tracker.log_activity("\u2705 Word document generated", event_type="found", phase="exporting")
        ctx.tracker.log_activity("🎉 Thesis generation complete!", event_type="milestone", phase="completed")

    if ctx.verbose:
        if pdf_generated:
            print(f"\u2705 Exported PDF: {requested_pdf_path}")
        else:
            print(f"⚠️ PDF not generated (fallback artifact: {final_md_path})")
        print(f"\u2705 Exported DOCX: {docx_path}")
        print(f"📂 Output folder: {ctx.folders['root']}")

    ctx.export_artifacts = {
        "pdf_generated": pdf_generated,
        "pdf_path": str(requested_pdf_path) if pdf_generated else None,
        "markdown_fallback_path": str(final_md_path),
        "docx_path": str(docx_path),
    }

    # Preserve function signature: when PDF is unavailable, return markdown path as first artifact.
    return (requested_pdf_path if pdf_generated else final_md_path), docx_path


# ---------------------------------------------------------------------------
# Helper functions (only used by compile phase)
# ---------------------------------------------------------------------------


def _strip_first_header(text: str) -> str:
    """Remove first line if it's a markdown header."""
    lines = text.strip().split('\n')
    if lines and lines[0].startswith('#'):
        return '\n'.join(lines[1:]).strip()
    return text.strip()


def normalize_conclusion_headings_for_final(conclusion_text: str, language: str) -> str:
    """Force generated conclusion markdown into the final chapter-6 namespace."""
    is_zh = language == "zh"
    title = "结论" if is_zh else "Conclusion"
    default_sections = (
        ["研究总结与管理启示", "研究局限与未来展望"]
        if is_zh
        else ["Summary and Implications", "Limitations and Future Research"]
    )
    lines = conclusion_text.strip().splitlines()
    normalized: list[str] = [f"# 6. {title}"]
    subsection_index = 0
    saw_subsection = False
    skipped_outer = False

    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not heading:
            normalized.append(line)
            continue

        hashes, raw = heading.groups()
        plain = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", raw.strip()).strip()
        plain_key = plain.lower()

        if len(hashes) == 1 and not skipped_outer and plain_key in {"结论", "conclusion", "conclusions"}:
            skipped_outer = True
            continue
        if len(hashes) == 1 and re.match(r"^[3-6]\.?\s+", raw.strip()):
            skipped_outer = True
            continue

        if len(hashes) >= 2:
            subsection_index += 1
            saw_subsection = True
            normalized.append(f"## 6.{subsection_index} {plain or default_sections[min(subsection_index - 1, len(default_sections) - 1)]}")
            continue

        normalized.append(line)

    body = "\n".join(normalized).strip()
    if not saw_subsection:
        rest = _strip_first_header(body)
        section = default_sections[0]
        body = f"# 6. {title}\n## 6.1 {section}"
        if rest.strip():
            body += f"\n{rest.strip()}"
        body += f"\n## 6.2 {default_sections[1]}"
    elif subsection_index == 1:
        body += f"\n## 6.2 {default_sections[1]}"
    body = re.sub(r"(?m)^##\s+[3-5]\.\d+(?:\.\d+)*\.?\s+", "## 6.1 ", body)
    return body.strip()


def _keep_docx_debug_artifacts() -> bool:
    return os.environ.get("OPENDRAFT_KEEP_DOCX_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def _select_compile_section_texts(ctx: DraftContext, clean_agent_output_func) -> tuple[str, str, str]:
    """Select compile inputs, using 02_main_body.md as the only body source."""
    drafts_dir = ctx.folders.get("drafts")
    exports_dir = ctx.folders.get("exports")

    if not drafts_dir:
        raise RuntimeError("Missing drafts folder; cannot assemble body in stable mode.")

    intro_file = drafts_dir / "01_introduction.md"
    main_body_file = drafts_dir / "02_main_body.md"
    conclusion_file = drafts_dir / "03_conclusion.md"

    if not main_body_file.exists():
        raise RuntimeError("Missing 02_main_body.md; cannot assemble body in stable mode.")

    intro_source = intro_file.read_text(encoding="utf-8") if intro_file.exists() else ctx.intro_output
    body_source = main_body_file.read_text(encoding="utf-8")
    conclusion_source = conclusion_file.read_text(encoding="utf-8") if conclusion_file.exists() else ctx.conclusion_output

    if exports_dir:
        _write_heading_debug_snapshot(ctx, "after_body_source_selected_headings.json", body_source, "after_body_source_selected")
        _write_heading_debug_snapshot(ctx, "after_conclusion_generated_headings.json", conclusion_source, "after_conclusion_generated")

    if ctx.language == "zh":
        from .compose import validate_main_body_outline

        validate_main_body_outline(body_source)

    intro_clean = _strip_first_header(clean_agent_output_func(intro_source))
    body_clean = clean_agent_output_func(body_source).strip()
    body_clean = _strip_duplicate_body_wrapper_heading(body_clean)
    if ctx.language == "zh":
        body_clean = normalize_main_body_headings_for_zh(body_clean)
    conclusion_clean = normalize_conclusion_headings_for_final(clean_agent_output_func(conclusion_source), ctx.language)

    if exports_dir:
        _write_heading_debug_snapshot(ctx, "after_main_body_normalized_headings.json", body_clean, "after_main_body_normalized")
        _write_heading_debug_snapshot(ctx, "after_conclusion_normalized_headings.json", conclusion_clean, "after_conclusion_normalized")

    return intro_clean, body_clean, conclusion_clean


def _strip_duplicate_body_wrapper_heading(text: str) -> str:
    lines = text.strip().splitlines()
    if not lines:
        return ""
    first = lines[0].strip()
    first_plain = re.sub(r"^#{1,6}\s*", "", first).strip()
    first_plain = re.sub(r"^\d+\.?\s*", "", first_plain).strip().lower()
    if first.startswith("#") and first_plain in {"main body", "body", "正文"}:
        return "\n".join(lines[1:]).strip()
    return text.strip()


def normalize_main_body_headings_for_zh(body_text: str) -> str:
    """Normalize the merged Chinese 02_main_body.md outline exactly once."""
    chapter_titles = {
        "1": ("2", "文献综述"),
        "2": ("3", "研究方法"),
        "3": ("4", "分析结果"),
        "4": ("5", "讨论"),
    }
    normalized_lines: list[str] = []

    for line in body_text.splitlines():
        top = re.match(r"^##\s+2\.(1|2|3|4)\.?\s+(.+?)\s*$", line)
        if top:
            new_number, new_title = chapter_titles[top.group(1)]
            normalized_lines.append(f"# {new_number}. {new_title}")
            continue

        nested = re.match(r"^###\s+2\.(1|2|3|4)\.(\d+)\.?\s+(.+?)\s*$", line)
        if nested:
            section, subsection, title = nested.groups()
            new_number, _ = chapter_titles[section]
            normalized_lines.append(f"## {new_number}.{subsection} {title}")
            continue

        deeper = re.match(r"^(#{4,6})\s+2\.(1|2|3|4)\.(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$", line)
        if deeper:
            hashes, section, rest, title = deeper.groups()
            new_number, _ = chapter_titles[section]
            normalized_lines.append(f"{hashes} {new_number}.{rest} {title}")
            continue

        normalized_lines.append(line)

    return "\n".join(normalized_lines).strip()


def _write_heading_debug_snapshot(ctx: DraftContext, filename: str, content: str, source_stage: str) -> None:
    exports_dir = ctx.folders.get("exports")
    if not exports_dir:
        return
    headings = _extract_markdown_heading_debug(content, source_stage)
    try:
        exports_dir.mkdir(parents=True, exist_ok=True)
        (exports_dir / filename).write_text(json.dumps(headings, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write heading debug snapshot %s: %s", filename, exc)


def _build_document_structure_manifest(content: str, language: str) -> dict[str, object]:
    """Build the semantic contract used by DOCX validation and post-processing."""
    from utils.document_ast import build_document_structure_manifest

    return build_document_structure_manifest(content, language)


def _extract_front_matter_metadata(content: str) -> dict[str, object]:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    metadata: dict[str, object] = {}
    for line in parts[1].splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("'\"")
    return metadata


def _write_document_structure_manifest(ctx: DraftContext, manifest: dict[str, object]) -> None:
    exports_dir = ctx.folders.get("exports")
    if not exports_dir:
        return
    try:
        exports_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(manifest, ensure_ascii=False, indent=2)
        (exports_dir / "document_structure_manifest.json").write_text(payload, encoding="utf-8")
        if _keep_docx_debug_artifacts():
            debug_dir = exports_dir / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / "document_structure_manifest.json").write_text(payload, encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write document structure manifest: %s", exc)


def _extract_markdown_heading_debug(content: str, source_stage: str) -> list[dict[str, object]]:
    headings: list[dict[str, object]] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        heading_text = match.group(2).strip()
        number = re.match(r"^(\d+(?:\.\d+)*)\.?\s+", heading_text)
        headings.append(
            {
                "line_no": line_no,
                "heading_level": len(match.group(1)),
                "heading_text": heading_text,
                "detected_number": number.group(1) if number else None,
                "source_stage": source_stage,
            }
        )
    return headings


def _assemble_markdown_body(
    ctx: DraftContext,
    intro_clean: str,
    body_clean: str,
    conclusion_clean: str,
    appendix_clean: str,
) -> str:
    """Assemble the structural markdown skeleton for the selected language."""
    is_zh = ctx.language == "zh"
    page = "<!-- PAGEBREAK -->"
    abstract_heading = "## 摘要" if is_zh else "## Abstract"
    abstract_placeholder = "[摘要将在编译阶段自动生成]" if is_zh else "[Abstract will be generated]"
    references_heading = "# 参考文献" if is_zh else "# References"

    if is_zh or ctx.language == "en" or ctx.academic_level == "research_paper":
        body_chapters = _promote_research_paper_body_chapters(body_clean, ctx.language)
        conclusion_heading = "# 6. 结论" if is_zh else "# 6. Conclusion"
        conclusion_body = _strip_first_header(conclusion_clean)
        appendix_heading = "# 附录" if is_zh else "# Appendices"
        appendix = f"\n\n{page}\n\n{appendix_heading}\n{appendix_clean}" if appendix_clean.strip() else ""
        return f"""{abstract_heading}
{abstract_placeholder}

{page}

# 1. {"引言" if is_zh else "Introduction"}
{intro_clean}

{page}

{body_chapters}

{page}

{conclusion_heading}
{conclusion_body}{appendix}

{page}

{references_heading}
[Citations will be compiled]"""

    labels = {
        "intro": "引言" if is_zh else "Introduction",
        "body": "正文" if is_zh else "Main Body",
        "conclusion": "结论" if is_zh else "Conclusion",
        "appendices": "附录" if is_zh else "Appendices",
    }
    return f"""{abstract_heading}
{abstract_placeholder}

{page}

# 1. {labels["intro"]}
{intro_clean}

{page}

# 2. {labels["body"]}
{body_clean}

{page}

# 3. {labels["conclusion"]}
{conclusion_clean}

{page}

# 4. {labels["appendices"]}
{appendix_clean}

{page}

# 5. {references_heading.lstrip("# ")}
[Citations will be compiled]"""


def _promote_research_paper_body_chapters(content: str, language: str) -> str:
    """Promote generated 2.x body sections to top-level research-paper chapters."""
    is_zh = language == "zh"
    if re.search(r"(?m)^#\s+[2-5]\.\s+", content):
        return _normalize_formal_academic_headings(content, language)
    chapter_names = (
        {
            "2.1": "文献综述",
            "2.2": "研究方法",
            "2.3": "分析结果",
            "2.4": "讨论",
        }
        if is_zh
        else {
            "2.1": "Literature Review",
            "2.2": "Methodology",
            "2.3": "Analysis and Results",
            "2.4": "Discussion",
        }
    )
    chapter_numbers = {"2.1": "2", "2.2": "3", "2.3": "4", "2.4": "5"}
    lines = []
    for line in content.splitlines():
        match = re.match(r"^#{1,6}\s+((?:2|1)\.(\d+))\.?\s+(.+?)\s*$", line)
        if match and match.group(2) in {"1", "2", "3", "4"}:
            key = f"2.{match.group(2)}"
            lines.append(f"# {chapter_numbers[key]}. {chapter_names[key]}")
            continue
        nested = re.match(r"^(#{2,6})\s+(?:2|1)\.(\d+)\.(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$", line)
        if nested and nested.group(2) in {"1", "2", "3", "4"}:
            _, section, rest, title = nested.groups()
            new_top = chapter_numbers[f"2.{section}"]
            lines.append(f"## {new_top}.{rest} {title}")
            continue
        if re.match(r"^#\s+(?:2\.?\s*)?(?:正文|Main Body|Body)\s*$", line, flags=re.IGNORECASE):
            continue
        lines.append(line)
    promoted = "\n".join(lines).strip()
    if not re.search(r"^#\s+2\.\s+", promoted, flags=re.MULTILINE):
        fallback = "文献综述" if is_zh else "Literature Review"
        return f"# 2. {fallback}\n{promoted}"
    return _normalize_formal_academic_headings(promoted, language)


def _normalize_formal_academic_headings(content: str, language: str) -> str:
    """Normalize top-level academic headings and cap heading depth at 3."""
    is_zh = language == "zh"
    top_titles = (
        {
            "1": "引言",
            "2": "文献综述",
            "3": "研究方法",
            "4": "分析结果",
            "5": "讨论",
            "6": "结论",
        }
        if is_zh
        else {
            "1": "Introduction",
            "2": "Literature Review",
            "3": "Methodology",
            "4": "Analysis and Results",
            "5": "Discussion",
            "6": "Conclusion",
        }
    )
    aliases = {
        "main body": "2",
        "body": "2",
        "正文": "2",
        "literature review": "2",
        "文献综述": "2",
        "methodology": "3",
        "methods": "3",
        "研究方法": "3",
        "analysis": "4",
        "results": "4",
        "analysis and results": "4",
        "results and analysis": "4",
        "分析结果": "4",
        "discussion": "5",
        "讨论": "5",
        "conclusion": "6",
        "conclusions": "6",
        "结论": "6",
    }

    normalized: list[str] = []
    for line in content.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            normalized.append(line)
            continue
        hashes, heading = match.groups()
        heading = re.sub(r"^[·•\-*]\s+", "", heading.strip())
        number_match = re.match(r"^(\d+)(?:\.(\d+(?:\.\d+)*))?\.?\s+(.+?)\s*$", heading)
        if number_match:
            top, rest, body = number_match.groups()
            body_key = body.strip().lower()
            if top in top_titles and (len(hashes) == 1 or rest is None):
                normalized.append(f"# {top}. {top_titles[top]}")
                continue
            if rest:
                parts = rest.split(".")
                level = len(parts) + 1
                normalized.append(f"{'#' * level} {top}.{'.'.join(parts)} {body}")
                continue
            if body_key in aliases:
                top = aliases[body_key]
                normalized.append(f"# {top}. {top_titles[top]}")
                continue
        plain = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading).strip()
        key = plain.lower()
        if key in aliases:
            top = aliases[key]
            normalized.append(f"# {top}. {top_titles[top]}")
            continue
        normalized.append(f"{hashes} {heading}")
    return "\n".join(normalized)


def _remove_forbidden_cover_metadata(content: str) -> str:
    """Remove generated_by from YAML and visible legacy cover metadata lines."""
    text = re.sub(r"(?im)^\s*generated_by\s*:.*$", "", content)
    forbidden_lines = [
        r"Title",
        r"题目\s*[:：]?",
        r"Generated by\s*[:：]?.*",
        r"生成工具\s*[:：]?.*",
        r"Disclaimer",
        r"This content is for research organization and writing reference only\.",
        r"使用声明\s*[:：]?.*",
        r"OpenDraft AI\s*-?\s*https://github\.com/federicodeponte/opendraft",
    ]
    for pattern in forbidden_lines:
        text = re.sub(rf"(?im)^\s*{pattern}\s*$", "", text)
    return re.sub(r"\n{4,}", "\n\n\n", text)


def _ensure_required_academic_top_headings(content: str, language: str) -> str:
    """Ensure the formal six-part thesis outline is present before references."""
    titles = (
        {
            "1": "引言",
            "2": "文献综述",
            "3": "研究方法",
            "4": "分析结果",
            "5": "讨论",
            "6": "结论",
        }
        if language == "zh"
        else {
            "1": "Introduction",
            "2": "Literature Review",
            "3": "Methodology",
            "4": "Analysis and Results",
            "5": "Discussion",
            "6": "Conclusion",
        }
    )
    present = set(re.findall(r"(?m)^#\s+([1-6])\.\s+", content))
    if not {"1", "2", "6"}.issubset(present):
        return content
    missing = [num for num in ["3", "4", "5"] if num not in present]
    if not missing:
        return content
    insertion = "\n\n".join(f"# {num}. {titles[num]}" for num in missing)
    conclusion_match = re.search(r"(?m)^#\s+6\.\s+", content)
    if conclusion_match:
        start = conclusion_match.start()
        return content[:start].rstrip() + "\n\n" + insertion + "\n\n" + content[start:]
    return content


def _normalize_heading_depth_and_numbering(content: str, language: str) -> str:
    """Cap formal heading depth by section and recompute numeric prefixes."""
    counters = [0, 0, 0]
    last_parent: dict[int, tuple[int, ...]] = {}
    current_top = ""
    in_intro = False
    in_references = False
    out: list[str] = []

    for line in content.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            out.append(line)
            continue

        hashes, raw_heading = match.groups()
        raw_depth = len(hashes)
        heading = raw_heading.strip()
        plain = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading).strip()
        plain_key = plain.lower()

        if plain_key in {"abstract", "摘要"}:
            current_top = "abstract"
            in_intro = False
            in_references = False
            out.append(f"# {plain}")
            continue
        if plain_key in {"references", "bibliography", "参考文献"}:
            current_top = "references"
            in_intro = False
            in_references = True
            out.append(f"# {plain}")
            continue

        top_number_match = re.match(r"^(\d+)(?:\.\d+)*\.?\s+", heading)
        top_number = top_number_match.group(1) if top_number_match else ""
        if raw_depth == 1 and top_number:
            current_top = top_number
            in_intro = top_number == "1" or plain_key in {"introduction", "引言"}
            in_references = False

        max_depth = 1 if in_references else 2 if in_intro else 3
        if raw_depth > max_depth:
            out.append(f"**{plain}**")
            continue

        depth = min(raw_depth, 3)
        if top_number or raw_depth == 1:
            if depth == 1:
                if top_number:
                    counters = [int(top_number), 0, 0]
                else:
                    counters[0] += 1
                    counters[1] = counters[2] = 0
                number = str(counters[0])
                current_top = number
                in_intro = number == "1" or plain_key in {"introduction", "引言"}
            else:
                existing_parts = [int(part) for part in re.findall(r"\d+", heading.split()[0])]
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
                for idx in range(depth, 3):
                    counters[idx] = 0
                number = ".".join(str(num) for num in counters[:depth])
            suffix = "." if depth == 1 else ""
            out.append(f"{'#' * depth} {number}{suffix} {plain}")
        else:
            out.append(f"{'#' * depth} {plain}")
    return "\n".join(out)


def _normalize_residual_citation_tokens(content: str, language: str) -> str:
    """Convert leftover citation wrappers and remove raw cite IDs from final output."""
    text = re.sub(r"\{\s*(\([^{}\n]*?(?:\d{4}|n\.d\.)[^{}\n]*?\))\s*\}", r"\1", content)
    text = re.sub(r"\{\{\s*\(([^{}\n]*?(?:\d{4}|n\.d\.)[^{}\n]*?)\)\s*\}\}", r"(\1)", text)
    text = re.sub(r"\{\{\s*([^{}\n]*?,\s*(?:\d{4}|n\.d\.))\s*\}\}", r"(\1)", text)
    text = re.sub(r"\{\s*([^{}\n]*?,\s*(?:\d{4}|n\.d\.))\s*\}", r"(\1)", text)
    text = re.sub(r"\{\{\s*cite_\d{3,}\s*\}\}", "", text)
    text = re.sub(r"\{\s*cite_\d{3,}\s*\}", "", text)
    text = re.sub(r"\bcite_\d{3,}\b", "", text)
    if language == "zh":
        text = re.sub(r"\{\s*\(([^{}\n]*?(?:\d{4}|n\.d\.)[^{}\n]*?)\)\s*\}", r"（\1）", text)
        text = re.sub(r"\(\s*([^()\n]*?,\s*(?:\d{4}|n\.d\.))\s*\)", r"（\1）", text)
        text = re.sub(r"([\u4e00-\u9fff])\s+（", r"\1（", text)
        text = re.sub(r"）\s+([。；，、])", r"）\1", text)
        text = re.sub(r"\s{2,}（", "（", text)
    else:
        text = re.sub(r"（([^（）\n]*?(?:et al\.|[A-Z][A-Za-z-]+)[^（）\n]*?,\s*(?:\d{4}|n\.d\.))）", r"(\1)", text)
    return text


def _localize_reference_list_heading(reference_list: str, language: str) -> str:
    if language != "zh":
        return reference_list
    return re.sub(r"(?im)^(#{1,6})\s*(?:References|Bibliography)\s*$", r"\1 参考文献", reference_list)


def _prepare_body_section(text: str, language: str) -> str:
    """Prepare the body section without dropping meaningful subsection headings."""
    cleaned = text.strip()
    if not cleaned:
        return cleaned

    lines = cleaned.split('\n')
    first = lines[0].strip() if lines else ""
    first_plain = re.sub(r"^#{1,6}\s*", "", first).strip()
    first_plain = re.sub(r"^\d+\.?\s*", "", first_plain).strip().lower()
    duplicate_body_names = {"main body", "body", "正文"}
    if first.startswith("#") and first_plain in duplicate_body_names:
        cleaned = "\n".join(lines[1:]).strip()

    from utils.text_utils import normalize_language_code

    if normalize_language_code(language) == "zh":
        cleaned = _normalize_chinese_body_outline(cleaned)
    return cleaned


def _normalize_chinese_body_outline(content: str) -> str:
    """Keep Chinese body headings under chapter 2 instead of restarting at 1.x."""
    lines = []
    in_body = False
    has_top_level_heading = any(re.match(r"^#\s+", candidate) for candidate in content.splitlines())
    for line in content.splitlines():
        heading = re.match(r"^(#{2,6})\s+(\d+(?:\.\d+)*\.?)\s+(.+?)\s*$", line)
        top_body = re.match(r"^#\s+2\.?\s*(?:正文|Main Body)\s*$", line, flags=re.IGNORECASE)
        top_next = re.match(r"^#\s+(?:3|4|5)\.?\s+", line)
        if top_body:
            in_body = True
            lines.append(line)
            continue
        if top_next:
            in_body = False

        if heading:
            hashes, number, title = heading.groups()
            number = number.rstrip(".")
            parts = number.split(".")
            if (in_body or not has_top_level_heading) and parts and parts[0] == "1" and len(parts) > 1:
                parts[0] = "2"
                number = ".".join(parts)
                if hashes.startswith("###"):
                    hashes = hashes[1:]
                line = f"{hashes} {number}. {title}"
        lines.append(line)
    return "\n".join(lines)


def _normalize_chinese_final_page_breaks(content: str) -> str:
    """Normalize visible page-break artifacts in final Chinese markdown."""
    return _normalize_markdown_page_breaks(content, output="comment")


def _normalize_markdown_page_breaks(content: str, output: str = "comment") -> str:
    """Normalize all internal page-break variants through PAGEBREAK markers."""
    replacement = "<!-- PAGEBREAK -->" if output == "comment" else r"\newpage"
    text = content
    markers = [
        r"(?im)^\s*\\\\?newpage\s*<!--\s*PAGEBREAK\s*-->\s*$",
        r"(?im)^\s*/newpage\s*<!--\s*PAGEBREAK\s*-->\s*$",
        r"(?im)^\s*ewpage\s*<!--\s*PAGEBREAK\s*-->\s*$",
        r"(?im)^\s*<!--\s*PAGEBREAK\s*-->\s*$",
        r"(?im)^\s*\\\\newpage\s*$",
        r"(?im)^\s*\\newpage\s*$",
        r"(?im)^\s*/newpage\s*$",
        r"(?im)^\s*ewpage\s*$",
        r"(?im)^\s*newpage\s*$",
    ]
    for pattern in markers:
        text = re.sub(pattern, lambda _m: replacement, text)
    return text


def collapse_duplicate_pagebreaks(content: str) -> str:
    """Collapse stacked page-break syntaxes to a single PAGEBREAK marker."""
    text = _normalize_markdown_page_breaks(content, output="comment")
    text = re.sub(
        r"(?is)(?:\s*(?:\\+newpage|/newpage|ewpage|newpage)?\s*<!--\s*PAGEBREAK\s*-->\s*){2,}",
        "\n\n<!-- PAGEBREAK -->\n\n",
        text,
    )
    text = re.sub(
        r"(?im)^\s*(?:\\+newpage|/newpage|ewpage|newpage)\s*\n\s*<!--\s*PAGEBREAK\s*-->\s*$",
        "<!-- PAGEBREAK -->",
        text,
    )
    text = re.sub(
        r"(?im)^\s*<!--\s*PAGEBREAK\s*-->\s*\n\s*(?:\\+newpage|/newpage|ewpage|newpage)\s*$",
        "<!-- PAGEBREAK -->",
        text,
    )
    return text


def _remove_compile_artifact_paragraphs(content: str) -> str:
    """Remove internal compile/planning notes before intermediate or final output."""
    forbidden = [
        "Concept alignment note:",
        "This paper explicitly operationalizes",
        "evidence-to-claim mapping",
    ]
    blocks = re.split(r"(\n\s*\n)", content)
    kept: list[str] = []
    skip_separator = False
    for idx in range(0, len(blocks), 2):
        block = blocks[idx]
        separator = blocks[idx + 1] if idx + 1 < len(blocks) else ""
        if any(marker in block for marker in forbidden):
            skip_separator = True
            continue
        if skip_separator and kept and separator:
            kept.append(separator)
            skip_separator = False
        kept.append(block)
        if separator:
            kept.append(separator)
    cleaned = "".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() + ("\n" if content.endswith("\n") else "")


def _localize_chinese_abstract_labels(content: str) -> str:
    """Localize structured abstract labels in final Chinese markdown."""
    replacements = {
        "Research Problem and Approach": "研究问题与方法",
        "Methodology and Findings": "研究方法与主要发现",
        "Key Contributions": "主要贡献",
        "Implications": "理论与实践意义",
        "Keywords": "关键词",
        "Abstract": "摘要",
    }
    text = content
    for source, target in replacements.items():
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    text = re.sub(r"(?im)^\*\*关键词\s*[:：]\*\*", "**关键词：**", text)
    text = re.sub(r"(?im)^(#{1,6})\s+摘要\s*$", r"\1 摘要", text)
    return text


def _apply_structure_aware_final_cleanup(
    content: str,
    language: str,
    paragraph_cleanup_func,
    paragraph_ai_cleanup_func,
) -> dict:
    """Clean only paragraph blocks while preserving structural Markdown blocks."""
    blocks = _parse_markdown_blocks(content)
    stats: dict[str, int] = {"paragraph_blocks": 0}
    out: list[str] = []
    in_references = False

    for block_type, block_text in blocks:
        if block_type == "heading":
            heading_plain = re.sub(r"^#{1,6}\s*", "", block_text.strip())
            heading_plain = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading_plain).strip().lower()
            if heading_plain in {"references", "bibliography", "参考文献"}:
                in_references = True
            out.append(block_text)
            continue
        if block_type == "paragraph" and not in_references and not _paragraph_has_protected_span(block_text):
            cleaned_result = paragraph_cleanup_func(block_text)
            cleaned = cleaned_result.get("text", block_text)
            for key, value in cleaned_result.get("stats", {}).items():
                stats[key] = stats.get(key, 0) + int(value)
            cleaned = paragraph_ai_cleanup_func(cleaned)
            out.append(cleaned)
            stats["paragraph_blocks"] += 1
            continue
        out.append(block_text)

    return {"text": "\n\n".join(part for part in out if part is not None), "stats": stats}


def _parse_markdown_blocks(content: str) -> list[tuple[str, str]]:
    """Parse Markdown into coarse blocks that final cleanup can protect."""
    lines = content.splitlines()
    blocks: list[tuple[str, str]] = []
    paragraph: list[str] = []
    idx = 0

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(("paragraph", "\n".join(paragraph).strip()))
            paragraph = []

    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            blocks.append(("front_matter", "\n".join(lines[: end + 1])))
            idx = end + 1

    in_code = False
    code_lines: list[str] = []
    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            code_lines = [line]
            in_code = True
            idx += 1
            while idx < len(lines):
                code_lines.append(lines[idx])
                if lines[idx].strip().startswith("```"):
                    idx += 1
                    break
                idx += 1
            blocks.append(("code_fence", "\n".join(code_lines)))
            in_code = False
            continue
        if not stripped:
            flush_paragraph()
            idx += 1
            continue
        if re.match(r"^#{1,6}\s+", line):
            flush_paragraph()
            blocks.append(("heading", line))
            idx += 1
            continue
        if re.match(r"^\s*<!--\s*PAGEBREAK\s*-->\s*$", line) or re.match(r"^\s*\\newpage\s*$", line):
            flush_paragraph()
            blocks.append(("page_break", line))
            idx += 1
            continue
        if _is_markdown_table_line(line):
            flush_paragraph()
            table_lines = [line]
            idx += 1
            while idx < len(lines) and _is_markdown_table_line(lines[idx]):
                table_lines.append(lines[idx])
                idx += 1
            blocks.append(("table", "\n".join(table_lines)))
            continue
        if _is_caption_line(line):
            flush_paragraph()
            blocks.append(("caption", line))
            idx += 1
            continue
        if _is_reference_line(line):
            flush_paragraph()
            blocks.append(("reference", line))
            idx += 1
            continue
        paragraph.append(line)
        idx += 1
    flush_paragraph()
    return blocks


def _is_markdown_table_line(line: str) -> bool:
    return bool(re.match(r"^\s*\|.*\|\s*$", line))


def _is_caption_line(line: str) -> bool:
    return bool(re.match(r"^\s*(?:表|Table)\s*\d+(?:[-‑–—]\d+)?(?:[.:：]|\s{2,}|\s+).*$", line.strip(), flags=re.IGNORECASE))


def _is_reference_line(line: str) -> bool:
    return bool(re.match(r"^\s*(?:[-*]\s+)?[A-Z\u4e00-\u9fff][^\n]+(?:https?://doi\.org/|doi:|DOI:)", line))


def _paragraph_has_protected_span(text: str) -> bool:
    protected_patterns = [
        r"https?://",
        r"\bdoi\.org/",
        r"\bDOI\b",
        r"\{cite_\d{3}\}",
        r"\[[^\]]+\]\([^)]+\)",
        r"\b(?:YOLOv?\d*|ESRGAN|SSD|CNN|SVM|PSNR|SSIM|RMSE|GAN|DOI)\b",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in protected_patterns)


def _normalize_yaml_language(content: str, language: str) -> str:
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content
    yaml_lines = parts[1].splitlines()
    replaced = False
    for idx, line in enumerate(yaml_lines):
        if re.match(r"^\s*language\s*:", line):
            yaml_lines[idx] = f'language: "{language}"'
            replaced = True
            break
    if not replaced:
        yaml_lines.append(f'language: "{language}"')
    return "---\n" + "\n".join(yaml_lines).strip("\n") + "\n---" + parts[2]


def _normalize_doi_url_case(content: str) -> str:
    return re.sub(r"https?://doi\.org/", "https://doi.org/", content, flags=re.IGNORECASE)


def _count_markdown_table_rows(content: str) -> int:
    return sum(1 for block_type, block_text in _parse_markdown_blocks(content) if block_type == "table" for line in block_text.splitlines() if "|" in line)


def _assert_markdown_table_rows_not_reduced(before: str, after: str) -> None:
    before_rows = _count_markdown_table_rows(before)
    after_rows = _count_markdown_table_rows(after)
    if after_rows < before_rows:
        raise ValueError(f"Markdown table rows were reduced during final cleanup: {before_rows} -> {after_rows}")


def finalize_or_repair_markdown(content: str, language: str) -> tuple[str, dict[str, object]]:
    report: dict[str, object] = {"fatal": False, "warnings": [], "auto_fixed": []}
    original = content
    text = content

    text = _normalize_markdown_page_breaks(text, output="comment")
    text = collapse_duplicate_pagebreaks(text)
    text = repair_pagebreaks(text)
    if text != original:
        _add_auto_fixed(report, "duplicate_pagebreak")

    before = text
    text = repair_heading_numbering(text, language, report)
    if text != before:
        _add_auto_fixed(report, "heading_numbering")

    before = text
    text = repair_table_captions(text, language)
    if text != before:
        _add_auto_fixed(report, "table_caption_numbering")

    before = text
    text = _normalize_residual_citation_tokens(text, language)
    if text != before:
        _add_auto_fixed(report, "citation_format_residue")

    validation = validate_final_markdown(text, language)
    _merge_format_report(report, validation)
    if validation.get("errors"):
        for message in validation["errors"]:
            _add_format_warning(report, "format_validation", message, "Continued export in repair_warn mode.")
    return text, report


def repair_pagebreaks(content: str) -> str:
    text = collapse_duplicate_pagebreaks(content)
    text = re.sub(r"(?is)(<!--\s*PAGEBREAK\s*-->\s*){2,}", "<!-- PAGEBREAK -->\n\n", text)
    text = re.sub(r"(?im)^\s*<!--\s*PAGEBREAK\s*-->\s*\n(?:\s*\n)*\s*<!--\s*PAGEBREAK\s*-->\s*$", "<!-- PAGEBREAK -->", text)
    return text


def repair_heading_numbering(content: str, language: str, report: Optional[dict[str, object]] = None) -> str:
    from utils.document_ast import normalize_document_markdown

    normalized, doc = normalize_document_markdown(content, language)
    for warning in doc.warnings:
        _add_format_warning(
            report,
            warning.get("type", "heading_structure"),
            warning.get("message", str(warning)),
            warning.get("action", "Repaired through document AST."),
        )
    return normalized


def _repair_missing_top_headings(content: str, canonical: dict[str, str], report: Optional[dict[str, object]]) -> str:
    top_numbers = [int(match.group(1)) for match in re.finditer(r"(?m)^#\s+([1-6])\.\s+", content)]
    if len(top_numbers) < 2:
        return content
    missing = [num for num in range(top_numbers[0], top_numbers[-1] + 1) if num not in top_numbers and str(num) in canonical]
    if not missing:
        return content
    text = content
    for num in reversed(missing):
        next_match = re.search(rf"(?m)^#\s+{num + 1}\.\s+", text)
        insertion = f"# {num}. {canonical[str(num)]}\n"
        if next_match:
            text = text[:next_match.start()].rstrip() + "\n\n" + insertion + "\n" + text[next_match.start():]
        else:
            text = text.rstrip() + "\n\n" + insertion
    _add_format_warning(report, "top_level_heading_numbers", f"Top-level heading numbers were not continuous: {top_numbers}.", "Inserted missing top-level headings.")
    _add_auto_fixed(report, "top_level_heading_numbering")
    return text


def repair_table_captions(content: str, language: str) -> str:
    caption_pattern = re.compile(
        r"(?im)^(?P<prefix>\s*)(?P<label>表\s*\d+(?:[.\-‑–—]\d+)?|Table\s+\d+(?:[.\-‑–—]\d+)?)(?P<sep>[.:：]|\s{2,})(?P<body>.*)$"
    )
    counter = 0
    mappings: dict[str, str] = {}

    def normalize_key(label: str) -> str:
        label = re.sub(r"\s+", "", label.strip(), flags=re.IGNORECASE)
        return label.lower()

    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        full_line = match.group(0).strip().strip("*_")
        if not _looks_like_table_caption_line(full_line, language):
            return match.group(0)
        counter += 1
        old_label = match.group("label")
        body = _strip_table_caption_prefix(full_line)
        if old_label:
            mappings.setdefault(normalize_key(old_label), str(counter))
        if language == "zh":
            return f"表{counter}" + (f"：{body}" if body else "")
        return f"Table {counter}" + (f". {body}" if body else "")

    text = caption_pattern.sub(repl, content)
    if not mappings:
        return text
    return _sync_table_reference_numbers(text, language, mappings, counter)


def _looks_like_table_caption_line(line: str, language: str) -> bool:
    match = re.match(
        r"^(?:表\s*\d+(?:[.\-‑–—]\d+)?|Table\s+\d+(?:[.\-‑–—]\d+)?)(?:(?P<punct>[.:：])|\s{2,})(?P<body>.*)$",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return False
    body = (match.group("body") or "").strip()
    if match.group("punct") or not body:
        return True
    if language == "zh" and re.match(r"^(?:总结|对比|归纳|显示|说明|表明|展示|列出|给出|呈现)了?", body):
        return False
    if language != "zh" and re.match(r"^(?:shows?|summari[sz]es|compares?|lists?|presents?|indicates?)\b", body, re.IGNORECASE):
        return False
    return True


def _strip_table_caption_prefix(line: str) -> str:
    body = re.sub(
        r"^(?:表\s*\d+(?:[.\-‑–—]\d+)?|Table\s+\d+(?:[.\-‑–—]\d+)?)(?:[.:：]|\s{2,}|\s+)?\s*",
        "",
        line.strip().strip("*_"),
        flags=re.IGNORECASE,
    ).strip()
    body = re.sub(
        r"^(?:表\s*\d+(?:[.\-‑–—]\d+)?|Table\s+\d+(?:[.\-‑–—]\d+)?)(?:[.:：]|\s{2,}|\s+)?\s*",
        "",
        body,
        flags=re.IGNORECASE,
    ).strip()
    body = re.sub(r"^[.:：]\s*", "", body)
    return body


def _sync_table_reference_numbers(text: str, language: str, mappings: dict[str, str], caption_count: int) -> str:
    if caption_count <= 0:
        return text

    def mapped_number(label: str) -> str:
        key = re.sub(r"\s+", "", label.strip(), flags=re.IGNORECASE).lower()
        mapped = mappings.get(key)
        if mapped:
            return mapped
        first = re.search(r"\d+", label)
        if not first:
            return "1"
        num = int(first.group(0))
        return str(min(max(num, 1), caption_count))

    out: list[str] = []
    for line in text.splitlines():
        if _looks_like_table_caption_line(line.strip().strip("*_"), language):
            out.append(line)
            continue
        if language == "zh":
            out.append(
                re.sub(
                    r"表\s*\d+(?:[.\-‑–—]\d+)?",
                    lambda match: f"表{mapped_number(match.group(0))}",
                    line,
                )
            )
        else:
            out.append(
                re.sub(
                    r"\bTable\s+\d+(?:[.\-‑–—]\d+)?\b",
                    lambda match: f"Table {mapped_number(match.group(0))}",
                    line,
                    flags=re.IGNORECASE,
                )
            )
    return "\n".join(out)


def validate_final_markdown(content: str, language: str) -> dict[str, object]:
    errors: list[str] = []
    warnings: list[dict[str, str]] = []
    if re.search(r"(?im)^\s*(?:ewpage|newpage|/newpage)\s*$", content):
        errors.append("Visible malformed page-break marker remains in final Markdown.")
    if re.search(r"(?im)^\s*\\\\?newpage\s*(?:<!--\s*PAGEBREAK\s*-->)?\s*$", content):
        errors.append("LaTeX page-break marker remains in final Markdown.")
    if re.search(r"(?im)^\s*(?:/newpage|ewpage)\s*<!--\s*PAGEBREAK\s*-->\s*$", content):
        errors.append("Malformed combined page-break marker remains in final Markdown.")
    for marker in ("Concept alignment note:", "This paper explicitly operationalizes", "evidence-to-claim mapping"):
        if marker in content:
            errors.append(f"Internal compile artifact remains in final Markdown: {marker}")
    if re.search(r"\{?\s*cite_\d{3,}\s*\}?", content):
        errors.append("Uncompiled cite_xxx token remains in final Markdown.")
    if re.search(r"\{\s*\([^{}\n]+?\)\s*\}", content):
        errors.append("Brace-wrapped author-year citation remains in final Markdown.")
    if re.search(r"(?im)^#\s+\d*\.?\s*Main Body\s*$", content):
        errors.append("Main Body placeholder heading remains in final Markdown.")
    _validate_toc_not_empty(content, errors)
    _validate_section_heading_depths(content, errors)
    for line in content.splitlines():
        match = re.match(r"^(#{4,6})\s+", line)
        if match:
            errors.append("Heading depth exceeds three levels.")
            break
    if "Https://doi.org" in content:
        errors.append("DOI URL case was corrupted: Https://doi.org")
    if language == "zh":
        forbidden = [
            "Research Problem and Approach",
            "Methodology and Findings",
            "Key Contributions",
            "Implications",
            "**Keywords",
            "\nTitle\n",
            "Document Type",
            "Generated by",
            "Disclaimer",
        ]
        for marker in forbidden:
            if marker in content:
                errors.append(f"Chinese final Markdown contains forbidden English/template label: {marker.strip()}")
        _validate_heading_outline(content, errors)
    else:
        _validate_english_sentence_initial_capitalization(content, errors)
    return {"errors": errors, "warnings": warnings, "auto_fixed": [], "fatal": False}


def _add_format_warning(report: Optional[dict[str, object]], warning_type: str, message: str, action: str) -> None:
    if report is None:
        return
    warnings = report.setdefault("warnings", [])
    if isinstance(warnings, list):
        item = {"type": warning_type, "message": message, "action": action}
        if item not in warnings:
            warnings.append(item)


def _add_auto_fixed(report: Optional[dict[str, object]], fix_name: str) -> None:
    if report is None:
        return
    auto_fixed = report.setdefault("auto_fixed", [])
    if isinstance(auto_fixed, list) and fix_name not in auto_fixed:
        auto_fixed.append(fix_name)


def _merge_format_report(target: dict[str, object], source: dict[str, object]) -> None:
    if source.get("fatal"):
        target["fatal"] = True
    for key in ("warnings", "errors"):
        for item in source.get(key, []) or []:
            if key == "errors":
                _add_format_warning(target, "format_validation", str(item), "Continued export in repair_warn mode.")
            else:
                warnings = target.setdefault("warnings", [])
                if isinstance(warnings, list) and item not in warnings:
                    warnings.append(item)
    for fix in source.get("auto_fixed", []) or []:
        _add_auto_fixed(target, str(fix))


def _record_format_stage_diagnostics(report: dict[str, object], content: str, language: str, stage: str) -> None:
    result = validate_final_markdown(content, language)
    for error in result.get("errors", []) or []:
        if "Duplicate numbered heading" in str(error) or "heading" in str(error).lower():
            _add_format_warning(report, f"{stage}_heading_diagnostic", str(error), "Will auto-repair before export.")


def _handle_format_validation_result(report: dict[str, object], verbose: bool) -> None:
    mode = os.environ.get("FORMAT_VALIDATION_MODE", "repair_warn").strip().lower() or "repair_warn"
    warnings = report.get("warnings", []) or []
    if mode == "strict" and warnings:
        messages = [item.get("message", str(item)) if isinstance(item, dict) else str(item) for item in warnings]
        raise ValueError("; ".join(messages))
    if verbose and warnings:
        for item in warnings[:5]:
            if isinstance(item, dict):
                print(f"⚠️ Format warning: {item.get('message', '')}")
                print(f"🔧 Auto-fixed: {item.get('action', 'Recorded warning.')}")
        print("✅ Continuing export...")


def _write_format_warnings_report(ctx: DraftContext, report: dict[str, object]) -> None:
    exports_dir = ctx.folders.get("exports")
    if not exports_dir:
        return
    try:
        exports_dir.mkdir(parents=True, exist_ok=True)
        (exports_dir / "format_warnings.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write format warnings report: %s", exc)


def _validate_final_markdown(content: str, language: str) -> None:
    result = validate_final_markdown(content, language)
    errors = result.get("errors") or []
    if errors:
        raise ValueError("; ".join(errors))


def _validate_toc_not_empty(content: str, errors: list[str]) -> None:
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if not re.match(r"^\s*#{0,6}\s*(?:目录|Table of Contents)\s*$", line, flags=re.IGNORECASE):
            continue
        entries = 0
        for following in lines[idx + 1:]:
            if re.match(r"^#\s+\d+\.?\s+", following):
                break
            if re.search(r"\b\d+(?:\.\d+)+\s+.+\s+\d+\s*$", following) or re.search(r"\t\d+\s*$", following):
                entries += 1
        if entries < 2:
            errors.append("TOC heading exists but has fewer than 2 entries before the next section.")


def _validate_section_heading_depths(content: str, errors: list[str]) -> None:
    current_top = ""
    seen_by_parent: dict[tuple[int, str], set[str]] = {}
    for line in content.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        level = len(match.group(1))
        heading = match.group(2).strip()
        number_match = re.match(r"^(\d+(?:\.\d+)*)\.?\s+", heading)
        if number_match:
            number = number_match.group(1)
            parts = number.split(".")
            if len(parts) == 1:
                current_top = parts[0]
            parent = ".".join(parts[:-1])
            key = (len(parts), parent)
            if number in seen_by_parent.setdefault(key, set()):
                errors.append(f"Duplicate numbered heading under the same parent: {number}")
            seen_by_parent[key].add(number)
        if current_top == "1" and level > 3:
            errors.append("Introduction contains heading deeper than ###.")
        if level >= 5:
            errors.append("Final Markdown contains a fifth-level heading.")


def _validate_english_sentence_initial_capitalization(content: str, errors: list[str]) -> None:
    in_references = False
    for block_type, block_text in _parse_markdown_blocks(content):
        if block_type == "heading":
            plain = re.sub(r"^#{1,6}\s*", "", block_text.strip())
            plain = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", plain).strip()
            in_references = plain.lower() in {"references", "bibliography"}
            if plain and plain[0].islower():
                errors.append(f"English heading starts with lowercase: {plain[:50]}")
                return
        if block_type == "paragraph" and not in_references and not re.match(r"^\s*https?://", block_text):
            first = re.search(r"[A-Za-z]", block_text)
            if first and block_text[first.start()].islower():
                errors.append(f"English paragraph starts with lowercase: {block_text[:50]}")
                return


def _validate_heading_outline(content: str, errors: list[str]) -> None:
    top_numbers = []
    for line in content.splitlines():
        match = re.match(r"^#\s+(\d+)\.\s+", line)
        if match:
            top_numbers.append(int(match.group(1)))
    if top_numbers:
        expected = list(range(top_numbers[0], top_numbers[-1] + 1))
        if top_numbers != expected:
            errors.append(f"Top-level heading numbers are not continuous: {top_numbers}")


def fix_single_line_tables(content: str) -> str:
    """
    Fix tables that LLM outputs on a single line.

    BUG #15: LLM sometimes generates tables as single concatenated lines:
    | Col1 | Col2 | | Row1 | Data | | Row2 | Data |
    """
    lines = content.split('\n')
    fixed_lines = []

    for line in lines:
        if line.strip().startswith('|') and re.search(r'\|\s*\|[:\w*]', line):
            parts = re.split(r'\| \|(?=\s*[:*\w-])', line)
            for part in parts:
                if part.strip():
                    fixed_part = part.strip()
                    if not fixed_part.startswith('|'):
                        fixed_part = '| ' + fixed_part
                    if not fixed_part.endswith('|'):
                        fixed_part = fixed_part + ' |'
                    fixed_lines.append(fixed_part)
        else:
            fixed_lines.append(line)

    return '\n'.join(fixed_lines)


def deduplicate_appendices(content: str) -> str:
    """Remove duplicate appendix sections from draft content."""
    appendix_pattern = re.compile(
        r"(## Appendix [A-Z]:.*?)(?=## Appendix [A-Z]:|## References|# \d+\.|$)",
        re.DOTALL,
    )

    seen_headers = set()
    matches = list(appendix_pattern.finditer(content))

    for match in reversed(matches):
        appendix_text = match.group(1)
        header_match = re.match(r"## Appendix ([A-Z]):", appendix_text)
        if header_match:
            header = header_match.group(1)
            if header in seen_headers:
                start, end = match.span()
                content = content[:start] + content[end:]
            else:
                seen_headers.add(header)

    return content


def clean_malformed_markdown(content: str) -> str:
    """
    Clean up common markdown formatting issues.

    Fixes orphaned code fences, multiple blank lines, trailing whitespace.
    """
    lines = content.split("\n")
    fence_count = 0
    fence_positions = []

    for i, line in enumerate(lines):
        if line.strip() == "```":
            fence_count += 1
            fence_positions.append(i)

    if fence_count % 2 == 1 and fence_positions:
        last_fence = fence_positions[-1]
        lines[last_fence] = ""

    content = "\n".join(lines)
    content = re.sub(r"\n{4,}", "\n\n\n", content)
    content = re.sub(r"[ \t]+$", "", content, flags=re.MULTILINE)

    return content


def _clean_chinese_final_artifacts(content: str) -> str:
    """Remove known Chinese-mode final-draft artifacts.

    Targets:
    - TOC leftovers (Table of Contents / 目录)
    - Abstract placeholders
    - Mixed-language template bridge sentences in body chapters
    """
    text = content

    # 1) Hard-remove placeholder artifacts
    text = re.sub(r'(?im)^\s*#+\s*(?:Table of Contents|目录)\s*$', '', text)
    text = re.sub(r'(?im)^\s*Table of Contents\s*$', '', text)
    text = re.sub(r'(?im)^\s*\[\s*Abstract\s+will\s+be\s+generated.*?\]\s*$', '', text)
    text = re.sub(r'(?im)^\s*\[\s*摘要将.*?\]\s*$', '', text)

    # 2) Remove common English template bridge sentences
    template_line_patterns = [
        r'(?im)^\s*The\s+findings\s+FROM\s+LITERATURE.*$',
        r'(?im)^\s*Compared\s+to\s+the\s+theoretical\s+framework\s+in\s+section.*$',
        r'(?im)^\s*As\s+discussed\s+in\s+section.*$',
    ]
    for pattern in template_line_patterns:
        text = re.sub(pattern, '', text)

    # 3) Chinese mode language consistency cleanup (skip references section)
    lines = text.split('\n')
    cleaned_lines = []
    in_references = False

    in_code_fence = False
    table_line_pattern = re.compile(r'^\s*\|.*\|\s*$')
    table_separator_pattern = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$')
    doi_or_url_pattern = re.compile(r'https?://|doi\.org/', re.IGNORECASE)
    technical_token_pattern = re.compile(r'\b(?:YOLOv?\d*|ESRGAN|SSD|CNN|SVM|PSNR|SSIM|RMSE|GAN|ML|ROI|SIA|DOI|Jetson)\b')

    for line in lines:
        if line.strip().startswith("```"):
            in_code_fence = not in_code_fence
            cleaned_lines.append(line)
            continue
        if re.match(r'^\s*#\s*\d+\.?\s*(参考文献|References|Bibliography)\s*$', line, flags=re.IGNORECASE):
            in_references = True

        if in_references:
            cleaned_lines.append(line)
            continue

        if (
            in_code_fence
            or table_line_pattern.match(line)
            or table_separator_pattern.match(line)
            or doi_or_url_pattern.search(line)
            or technical_token_pattern.search(line)
        ):
            cleaned_lines.append(line)
            continue

        plain = line.strip()
        if not plain:
            cleaned_lines.append(line)
            continue

        cjk_chars = len(re.findall(r'[\u4e00-\u9fff]', plain))
        latin_chars = len(re.findall(r'[A-Za-z]', plain))
        total_signal = cjk_chars + latin_chars

        # Remove obviously English-template lines in Chinese body text.
        if total_signal >= 40 and cjk_chars > 0:
            latin_ratio = latin_chars / max(1, total_signal)
            if latin_ratio > 0.6:
                continue

        cleaned_lines.append(line)

    text = '\n'.join(cleaned_lines)
    text = re.sub(r'\n{3,}', '\n\n', text).strip() + '\n'
    return text
