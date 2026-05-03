#!/usr/bin/env python3
"""
DOCX post-processing for production OpenDraft exports.

Pandoc handles the heavy markdown conversion. This module fixes the Word-native
details Pandoc cannot reliably infer from generated academic drafts: language
cover pages, TOC fields, page breaks, captions, tables, styles, and page numbers.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import shutil
import subprocess
from typing import Optional, Dict, Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt


ZH_EAST_ASIA_FONT = "Noto Serif CJK SC"
ZH_LATIN_FONT = "Times New Roman"
EN_BODY_FONT = "Times New Roman"


def insert_academic_structure(
    docx_path: Path,
    verbose: bool = False,
    options: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """Apply production DOCX post-processing and return telemetry stats."""
    options = options or {}
    language = "zh" if str(options.get("language", "en")).lower().startswith("zh") else "en"
    stats: dict[str, Any] = {
        "tables_processed": 0,
        "docx_tables_detected": 0,
        "captions_generated": 0,
        "warnings": [],
        "validation_errors": [],
    }

    if not docx_path.exists():
        raise FileNotFoundError(f"DOCX file not found: {docx_path}")

    try:
        if verbose:
            print(f"📄 Post-processing DOCX: {docx_path.name}")

        doc = Document(docx_path)
        _configure_document_styles(doc, language)
        _configure_sections(doc, language)
        _clean_visible_residue(doc, language)
        _remove_initial_pandoc_title_block(doc)
        _insert_cover_page(doc, options, language)
        _remove_existing_toc(doc)
        _normalize_headings(doc, language)
        _style_captions_and_notes(doc, language, stats)
        _style_tables(doc, language, stats)
        _page_break_before_references(doc, language)
        _page_break_after_abstract(doc, language)
        _validate_no_math_loss(doc)
        _validate_phase_one_docx(doc, language, int(options.get("markdown_tables_detected") or 0), stats)

        doc.save(docx_path)
        _update_fields_with_libreoffice(docx_path, stats)

        if verbose:
            print(
                "   ✅ Post-processing complete "
                f"({stats['tables_processed']} tables, {stats['captions_generated']} captions)"
            )
        return stats
    except Exception as exc:
        if verbose:
            print(f"   ❌ Post-processing failed: {exc}")
            import traceback
            traceback.print_exc()
        return {}


def _configure_document_styles(doc: Document, language: str) -> None:
    # The reference DOCX is the style source of truth. Post-processing only
    # creates missing helper styles so older/custom reference docs still export.
    _ensure_paragraph_style(doc, "Caption", language, size=10, bold=False, only_if_missing=True)
    _ensure_paragraph_style(doc, "Table Note", language, size=9, bold=False, only_if_missing=True)
    _ensure_paragraph_style(doc, "References", language, size=11, bold=False, only_if_missing=True)
    _ensure_paragraph_style(doc, "Abstract Label", language, size=12, bold=True, only_if_missing=True)
    _ensure_paragraph_style(doc, "Keywords", language, size=12, bold=False, only_if_missing=True)


def _set_style_font(style, language: str, size: Optional[int] = None, bold: Optional[bool] = None, italic: Optional[bool] = None) -> None:
    font = style.font
    font.name = ZH_EAST_ASIA_FONT if language == "zh" else EN_BODY_FONT
    if size:
        font.size = Pt(size)
    elif font.size is None:
        font.size = Pt(10.5 if language == "zh" else 11)
    if bold is not None:
        font.bold = bold
    if italic is not None:
        font.italic = italic
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ZH_LATIN_FONT if language == "zh" else EN_BODY_FONT)
    rfonts.set(qn("w:hAnsi"), ZH_LATIN_FONT if language == "zh" else EN_BODY_FONT)
    rfonts.set(qn("w:eastAsia"), ZH_EAST_ASIA_FONT if language == "zh" else EN_BODY_FONT)


def _set_run_font(run, language: str) -> None:
    run.font.name = ZH_EAST_ASIA_FONT if language == "zh" else EN_BODY_FONT
    rpr = run._r.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ZH_LATIN_FONT if language == "zh" else EN_BODY_FONT)
    rfonts.set(qn("w:hAnsi"), ZH_LATIN_FONT if language == "zh" else EN_BODY_FONT)
    rfonts.set(qn("w:eastAsia"), ZH_EAST_ASIA_FONT if language == "zh" else EN_BODY_FONT)


def _ensure_paragraph_style(
    doc: Document,
    name: str,
    language: str,
    size: int,
    bold: bool,
    only_if_missing: bool = False,
) -> None:
    if name in doc.styles:
        if only_if_missing:
            return
        style = doc.styles[name]
    else:
        style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    _set_style_font(style, language, size=size, bold=bold)


def _configure_sections(doc: Document, language: str) -> None:
    for section in doc.sections:
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)
        section.left_margin = Cm(2.8 if language == "zh" else 2.54)
        section.right_margin = Cm(2.8 if language == "zh" else 2.54)


def _clean_visible_residue(doc: Document, language: str) -> None:
    for para in doc.paragraphs:
        text = para.text
        if not text:
            continue
        cleaned = text
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\{\s*(\([^{}\n]+?\))\s*\}", r"\1", cleaned)
        cleaned = re.sub(r"(?i)https://doi\.org/", "https://doi.org/", cleaned)
        cleaned = re.sub(r"(?i)http://doi\.org/", "https://doi.org/", cleaned)
        if language == "zh":
            cleaned = cleaned.replace("Table of Contents", "")
            cleaned = cleaned.replace("Research Problem and Approach", "研究问题与研究方法")
            cleaned = cleaned.replace("Methodology and Findings", "研究方法与主要发现")
            cleaned = cleaned.replace("Key Contributions", "主要贡献")
            cleaned = cleaned.replace("Implications", "理论与现实意义")
            cleaned = re.sub(r"\bKeywords\s*[:：]", "关键词：", cleaned)
        cleaned = re.sub(r"Right-click and update field to refresh the table of contents\.", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\\+newpage|/newpage|\bewpage\b|\bnewpage\b", "", cleaned, flags=re.IGNORECASE)
        if (para.style.name if para.style else "").startswith("Heading"):
            cleaned = re.sub(r"^[·•\-*]\s+(?=\d+(?:\.\d+)*\.?\s+)", "", cleaned.strip())
        if cleaned != text:
            _replace_paragraph_text(para, cleaned)


def _remove_initial_pandoc_title_block(doc: Document) -> None:
    for para in list(doc.paragraphs[:12]):
        style = para.style.name if para.style else ""
        if style in {"Title", "Subtitle", "Author", "Date"}:
            _delete_paragraph(para)


def _insert_cover_page(doc: Document, options: dict[str, Any], language: str) -> None:
    first = doc.paragraphs[0] if doc.paragraphs else doc.add_paragraph()
    title = options.get("title") or _first_nonempty_heading(doc) or ("文稿题目" if language == "zh" else "Title")
    date = options.get("date") or datetime.now().strftime("%Y-%m-%d")
    generated_by = options.get("system_credit") or "OpenDraft AI"

    if language == "zh":
        lines = [
            ("题目", 12, False),
            (title, 20, True),
            (f"文稿类型：{options.get('project_type') or '研究参考稿'}", 12, False),
            (f"生成工具：{generated_by}", 11, False),
            (f"生成日期：{_format_chinese_cover_date(str(date))}", 11, False),
            ("使用声明：本内容仅供资料梳理和写作参考，不构成正式学术成果。", 10, False),
        ]
    else:
        lines = [
            ("Title", 11, False),
            (title, 18, True),
            (f"Document Type: {options.get('project_type') or 'Research Paper Draft'}", 12, False),
            (f"Generated by: {generated_by}", 11, False),
            (f"Date: {date}", 11, False),
            ("Disclaimer: This content is for research organization and writing reference only.", 10, False),
        ]

    inserted = []
    for text, size, bold in lines:
        para = first.insert_paragraph_before(text)
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.paragraph_format.first_line_indent = None
        para.paragraph_format.space_after = Pt(10 if bold else 6)
        for run in para.runs:
            _set_run_font(run, language)
            run.font.size = Pt(size)
            run.font.bold = bold
        inserted.append(para)
    spacer = first.insert_paragraph_before("")
    spacer.paragraph_format.space_after = Pt(24)
    inserted.append(spacer)
    inserted[-1].add_run().add_break(WD_BREAK.PAGE)


def _format_chinese_cover_date(date_text: str) -> str:
    match = re.match(r"^(\d{4})[-/年](\d{1,2})(?:[-/月]\d{1,2})?", date_text.strip())
    if match:
        year, month = match.groups()
        return f"{year}年{int(month)}月"
    return date_text


def _ensure_toc(doc: Document, language: str) -> None:
    toc_title = "目录" if language == "zh" else "Table of Contents"
    existing = [para for para in doc.paragraphs if para.text.strip() in {"Table of Contents", "目录"} or _paragraph_has_toc_field(para)]
    keep = existing[0] if existing else None
    for para in existing[1:]:
        _delete_paragraph(para)
    if keep:
        _replace_paragraph_text(keep, toc_title)
        keep.style = doc.styles["Heading 1"]
        keep.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _remove_numbering_from_paragraph(keep)
        _ensure_page_break_after(keep)
        return

    insert_before = _find_first_content_paragraph(doc)
    toc_heading = insert_before.insert_paragraph_before(toc_title) if insert_before else doc.add_paragraph(toc_title)
    toc_heading.style = doc.styles["Heading 1"]
    toc_heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _remove_numbering_from_paragraph(toc_heading)
    _ensure_page_break_after(toc_heading)


def _remove_existing_toc(doc: Document) -> None:
    """Remove visible/field TOC leftovers until real updated TOC support is stable."""
    for para in list(doc.paragraphs):
        if para.text.strip() in {"Table of Contents", "目录"} or _paragraph_has_toc_field(para):
            _delete_paragraph(para)


def _find_toc_title_paragraph(doc: Document):
    for para in doc.paragraphs:
        if para.text.strip() in {"Table of Contents", "目录"} or _paragraph_has_toc_field(para):
            return para if para.text.strip() else _previous_paragraph(doc, para)
    return None


def _paragraph_has_toc_field(para) -> bool:
    xml = para._p.xml
    return "TOC" in xml and ("instrText" in xml or "fldSimple" in xml)


def _append_toc_field(para) -> None:
    run = para.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = r'TOC \o "1-3" \h \z \u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = ""
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.extend([fld_begin, instr, fld_sep, text, fld_end])


def _normalize_headings(doc: Document, language: str) -> None:
    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style else ""
        if not text:
            continue
        if _is_reference_heading(text, language):
            if language == "zh":
                text = "参考文献"
                _replace_paragraph_text(para, text)
            para.style = doc.styles["Heading 1"]
            _remove_numbering_from_paragraph(para)
            continue
        if not style.startswith("Heading"):
            continue
        clean = re.sub(r"^[·•\-*]\s+(?=\d+(?:\.\d+)*\.?\s+)", "", text)
        unnumbered_candidate = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", clean).strip()
        if _is_unnumbered_heading(unnumbered_candidate, language):
            clean = unnumbered_candidate
            _remove_numbering_from_paragraph(para)
        else:
            _remove_numbering_from_paragraph(para)
        if clean != text:
            _replace_paragraph_text(para, clean)


def _is_reference_heading(text: str, language: str) -> bool:
    plain = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", text.strip()).lower()
    return plain in {"references", "bibliography", "参考文献"}


def _is_unnumbered_heading(text: str, language: str) -> bool:
    plain = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", text.strip()).lower()
    if plain in {"abstract", "table of contents", "references", "bibliography", "appendix", "appendices"}:
        return True
    if plain.startswith("appendix "):
        return True
    return language == "zh" and plain in {"摘要", "目录", "参考文献", "附录"}


def _apply_heading_numbering(para, level: int) -> None:
    """Attach numPr to headings so Word sees multilevel numbering intent."""
    ppr = para._p.get_or_add_pPr()
    num_pr = ppr.find(qn("w:numPr"))
    if num_pr is None:
        num_pr = OxmlElement("w:numPr")
        ppr.append(num_pr)
    ilvl = num_pr.find(qn("w:ilvl"))
    if ilvl is None:
        ilvl = OxmlElement("w:ilvl")
        num_pr.append(ilvl)
    ilvl.set(qn("w:val"), str(level - 1))
    num_id = num_pr.find(qn("w:numId"))
    if num_id is None:
        num_id = OxmlElement("w:numId")
        num_pr.append(num_id)
    num_id.set(qn("w:val"), "1")


def _remove_numbering_from_paragraph(para) -> None:
    ppr = para._p.pPr
    if ppr is None:
        return
    num_pr = ppr.find(qn("w:numPr"))
    if num_pr is not None:
        ppr.remove(num_pr)


def _style_captions_and_notes(doc: Document, language: str, stats: dict[str, Any]) -> None:
    caption_seen = set()
    caption_counter = 0
    table_block_indices = _table_block_indices(doc)
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if (
            len(text) < 120
            and re.match(r"^(表|Table)\s+\d+(?:[-‑–—]\d+)?(?:[.:：]|\s{2,}|\s+[^A-Za-z])", text, re.IGNORECASE)
            and _has_nearby_table(doc, para, table_block_indices, radius=2)
        ):
            caption_counter += 1
            body = re.sub(r"^(表|Table)\s+\d+(?:[-‑–—]\d+)?[.:：]?\s*", "", text, flags=re.IGNORECASE).strip()
            body = re.sub(r"^(表|Table)\s+\d+(?:[-‑–—]\d+)?[.:：]?\s*", "", body, flags=re.IGNORECASE).strip()
            if language == "zh":
                new_text = f"表 {caption_counter}" + (f"  {body}" if body else "")
            else:
                new_text = f"Table {caption_counter}" + (f". {body}" if body else "")
            _replace_paragraph_text(para, new_text)
            para.style = doc.styles["Caption"]
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            para.paragraph_format.keep_with_next = True
            para.paragraph_format.space_before = Pt(6)
            para.paragraph_format.space_after = Pt(3)
            caption_seen.add(caption_counter)
        elif text.startswith("注：") or text.startswith("Note:"):
            para.style = doc.styles["Table Note"]
            para.paragraph_format.space_before = Pt(3)
            para.paragraph_format.space_after = Pt(6)
    stats["captions_generated"] = len(caption_seen)


def _body_block_index(doc: Document, element) -> Optional[int]:
    body_children = list(doc.element.body.iterchildren())
    for idx, child in enumerate(body_children):
        if child is element:
            return idx
    return None


def _table_block_indices(doc: Document) -> set[int]:
    indices = set()
    for table in doc.tables:
        idx = _body_block_index(doc, table._tbl)
        if idx is not None:
            indices.add(idx)
    return indices


def _has_nearby_table(doc: Document, para, table_indices: set[int], radius: int) -> bool:
    para_idx = _body_block_index(doc, para._p)
    if para_idx is None:
        return False
    return any(abs(table_idx - para_idx) <= radius for table_idx in table_indices)


def _style_tables(doc: Document, language: str, stats: dict[str, Any]) -> None:
    for table in doc.tables:
        num_cols = len(table.columns)
        _set_table_width_pct(table, 5000)
        _set_table_borders(table)
        table.autofit = True
        if num_cols > 4:
            _mark_landscape_section(table)

        font_size = Pt(9.5 if language == "zh" else 9) if num_cols > 4 else Pt(10 if language == "zh" else 10)
        for row_idx, row in enumerate(table.rows):
            if row_idx == 0:
                _repeat_table_header(row)
            _prevent_row_split(row)
            for cell in row.cells:
                _set_cell_margins(cell, top=80, bottom=80, left=80, right=80)
                if row_idx == 0:
                    _shade_cell(cell, "EDEDED")
                for para in cell.paragraphs:
                    para.paragraph_format.first_line_indent = None
                    para.paragraph_format.space_before = Pt(2)
                    para.paragraph_format.space_after = Pt(2)
                    for run in para.runs:
                        _set_run_font(run, language)
                        run.font.size = font_size
                        run.font.bold = row_idx == 0
        stats["tables_processed"] += 1
    stats["docx_tables_detected"] = len(doc.tables)


def _set_table_width_pct(table, width: int) -> None:
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(width))
    tbl_w.set(qn("w:type"), "pct")
    jc = tbl_pr.find(qn("w:jc"))
    if jc is None:
        jc = OxmlElement("w:jc")
        tbl_pr.append(jc)
    jc.set(qn("w:val"), "center")


def _set_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        elem = borders.find(qn(f"w:{edge}"))
        if elem is None:
            elem = OxmlElement(f"w:{edge}")
            borders.append(elem)
        elem.set(qn("w:val"), "single")
        elem.set(qn("w:sz"), "4")
        elem.set(qn("w:space"), "0")
        elem.set(qn("w:color"), "666666")


def _repeat_table_header(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:tblHeader")) is None:
        tr_pr.append(OxmlElement("w:tblHeader"))


def _prevent_row_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    if tr_pr.find(qn("w:cantSplit")) is None:
        tr_pr.append(OxmlElement("w:cantSplit"))


def _set_cell_margins(cell, top: int, bottom: int, left: int, right: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    margins = tc_pr.find(qn("w:tcMar"))
    if margins is None:
        margins = OxmlElement("w:tcMar")
        tc_pr.append(margins)
    for edge, value in {"top": top, "bottom": bottom, "left": left, "right": right}.items():
        node = margins.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            margins.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def _shade_cell(cell, fill: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def _mark_landscape_section(table) -> None:
    # Word section wrapping around individual tables is fragile through
    # python-docx. Marking the table with a wide-table property lets tests and
    # downstream repair code identify it, while width/font fixes keep it usable.
    tbl_pr = table._tbl.tblPr
    desc = tbl_pr.find(qn("w:tblDescription"))
    if desc is None:
        desc = OxmlElement("w:tblDescription")
        tbl_pr.append(desc)
    desc.set(qn("w:val"), "wide-table-landscape-candidate")


def _page_break_after_abstract(doc: Document, language: str) -> None:
    in_abstract = False
    last = None
    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style else ""
        if text in {"Abstract", "摘要"} and style.startswith("Heading"):
            in_abstract = True
            continue
        if in_abstract and style == "Heading 1" and text and text not in {"目录", "Table of Contents"}:
            if last is not None:
                _ensure_page_break_after(last)
            return
        if in_abstract:
            last = para


def _page_break_before_references(doc: Document, language: str) -> None:
    for para in doc.paragraphs:
        if _is_reference_heading(para.text.strip(), language):
            _ensure_page_break_before(para)
            for following in _iter_following_paragraphs(doc, para):
                if not following.text.strip():
                    continue
                if (following.style.name if following.style else "") == "Heading 1":
                    break
                following.style = doc.styles["References"]
            return


def _add_page_numbers(doc: Document) -> None:
    for idx, section in enumerate(doc.sections):
        footer = section.footer
        para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.text = ""
        run = para.add_run()
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = "PAGE"
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        run._r.extend([fld_begin, instr, fld_end])


def _validate_no_math_loss(doc: Document) -> None:
    text = "\n".join(para.text for para in doc.paragraphs)
    if re.search(r"\bthermal gradients\s*\(\)", text, flags=re.IGNORECASE):
        raise ValueError("Empty thermal gradient placeholder detected in DOCX: thermal gradients ()")
    if re.search(r"\bgrowth velocity\s*\(\)", text, flags=re.IGNORECASE):
        raise ValueError("Empty growth velocity placeholder detected in DOCX: growth velocity ()")
    if re.search(r"\bcooling rates exceeding\s+K/s\b", text, flags=re.IGNORECASE):
        raise ValueError("Missing numeric value before K/s in DOCX")
    if re.search(r"\bresulting\s+ratio\b", text, flags=re.IGNORECASE):
        raise ValueError("Missing variable before ratio in DOCX")


def _validate_phase_one_docx(doc: Document, language: str, markdown_tables_detected: int, stats: dict[str, Any]) -> None:
    stats["docx_tables_detected"] = len(doc.tables)
    errors: list[str] = []
    texts = [para.text.strip() for para in doc.paragraphs if para.text.strip()]
    full_text = "\n".join(texts)

    if markdown_tables_detected > 0 and len(doc.tables) == 0:
        errors.append(
            f"Markdown tables were detected before export ({markdown_tables_detected}), "
            "but the generated DOCX contains no Word tables."
        )

    residue_patterns = [
        r"\bewpage\b",
        r"\\+newpage",
        r"\bTable\s+\d+\s*\|",
        r"\b表\s*\d+\s*\|",
    ]
    for pattern in residue_patterns:
        if re.search(pattern, full_text, flags=re.IGNORECASE):
            errors.append(f"Visible export residue remains in DOCX: {pattern}")

    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style else ""
        if style.startswith("Heading") and re.match(r"^(?:表|Table)\s*\d+", text, re.IGNORECASE):
            errors.append(f"Table caption is still styled as a heading: {text}")

    if language == "zh":
        forbidden = [
            "Title",
            "Document Type",
            "Generated by",
            "Disclaimer",
            "OPENDRAFT UNIVERSITY",
            "Faculty of Engineering",
            "Department of Computer Science",
        ]
        for marker in forbidden:
            if re.search(rf"(^|\n)\s*{re.escape(marker)}(?:\s*[:：].*)?(\n|$)", full_text, flags=re.IGNORECASE):
                errors.append(f"English cover/template residue remains in Chinese DOCX: {marker}")

    if errors:
        stats["validation_errors"].extend(errors)
        raise ValueError("; ".join(errors))


def _update_fields_with_libreoffice(docx_path: Path, stats: dict[str, Any]) -> None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        stats["warnings"].append("LibreOffice not found; Word fields were inserted but not refreshed server-side.")
        return
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                str(docx_path.parent),
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except Exception as exc:
        stats["warnings"].append(f"LibreOffice field update failed: {exc}")


def _first_nonempty_heading(doc: Document) -> Optional[str]:
    for para in doc.paragraphs:
        text = para.text.strip()
        if text and (para.style.name if para.style else "").startswith("Heading"):
            return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", text)
    return None


def _find_first_content_paragraph(doc: Document):
    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style else ""
        if text and style.startswith("Heading") and text not in {"Table of Contents", "目录"}:
            return para
    return doc.paragraphs[0] if doc.paragraphs else None


def _ensure_page_break_after(para) -> None:
    if "w:type=\"page\"" in para._p.xml:
        return
    para.add_run().add_break(WD_BREAK.PAGE)


def _ensure_page_break_before(para) -> None:
    ppr = para._p.get_or_add_pPr()
    if ppr.find(qn("w:pageBreakBefore")) is None:
        ppr.append(OxmlElement("w:pageBreakBefore"))


def _insert_paragraph_after(after_para, text: str):
    new_p = OxmlElement("w:p")
    after_para._p.addnext(new_p)
    from docx.text.paragraph import Paragraph
    return Paragraph(new_p, after_para._parent)


def _delete_paragraph(para) -> None:
    element = para._element
    element.getparent().remove(element)
    para._p = para._element = None


def _replace_paragraph_text(para, text: str) -> None:
    for run in list(para.runs):
        run._element.getparent().remove(run._element)
    if text:
        para.add_run(text)


def _previous_paragraph(doc: Document, para):
    previous = None
    for candidate in doc.paragraphs:
        if candidate._p is para._p:
            return previous
        previous = candidate
    return None


def _iter_following_paragraphs(doc: Document, para):
    found = False
    for candidate in doc.paragraphs:
        if found:
            yield candidate
        elif candidate._p is para._p:
            found = True


def _next_heading_after(doc: Document, para):
    for candidate in _iter_following_paragraphs(doc, para):
        if (candidate.style.name if candidate.style else "").startswith("Heading"):
            return candidate
    return None


def main() -> None:
    import sys

    if len(sys.argv) < 2:
        print("Usage: python docx_post_processor.py <docx-path>")
        raise SystemExit(1)

    result = insert_academic_structure(Path(sys.argv[1]), verbose=True, options={"language": "en"})
    raise SystemExit(0 if result else 1)


if __name__ == "__main__":
    main()
