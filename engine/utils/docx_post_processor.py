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


def insert_academic_structure(
    docx_path: Path,
    verbose: bool = False,
    options: Optional[Dict[str, Any]] = None,
) -> dict[str, Any]:
    """Apply production DOCX post-processing and return telemetry stats."""
    options = options or {}
    language = "zh" if str(options.get("language", "en")).lower().startswith("zh") else "en"
    stats: dict[str, Any] = {"tables_processed": 0, "captions_generated": 0, "warnings": []}

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
        _ensure_toc(doc, language)
        _normalize_headings(doc, language)
        _style_captions_and_notes(doc, language, stats)
        _style_tables(doc, language, stats)
        _page_break_before_references(doc, language)
        _page_break_after_abstract(doc, language)
        _add_page_numbers(doc)
        _validate_no_math_loss(doc)

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
    normal = doc.styles["Normal"]
    _set_style_font(normal, language)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0 if language == "zh" else 6)
    normal.paragraph_format.line_spacing = 1.5 if language == "zh" else 1.15
    normal.paragraph_format.first_line_indent = Cm(0.74) if language == "zh" else None

    for style_name in ("Body Text", "First Paragraph"):
        if style_name in doc.styles:
            style = doc.styles[style_name]
            _set_style_font(style, language)
            style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            style.paragraph_format.line_spacing = normal.paragraph_format.line_spacing
            style.paragraph_format.space_after = normal.paragraph_format.space_after

    heading_specs = {
        "Heading 1": (16 if language == "zh" else 14, True, False, WD_ALIGN_PARAGRAPH.LEFT),
        "Heading 2": (14 if language == "zh" else 13, True, False, WD_ALIGN_PARAGRAPH.LEFT),
        "Heading 3": (12, True, False, WD_ALIGN_PARAGRAPH.LEFT),
        "Heading 4": (11, True, True, WD_ALIGN_PARAGRAPH.LEFT),
    }
    for style_name, (size, bold, italic, align) in heading_specs.items():
        if style_name not in doc.styles:
            continue
        style = doc.styles[style_name]
        _set_style_font(style, language, size=size, bold=bold, italic=italic)
        style.paragraph_format.alignment = align
        style.paragraph_format.first_line_indent = None
        style.paragraph_format.space_before = Pt(12)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.keep_with_next = True

    _ensure_paragraph_style(doc, "Caption", language, size=10, bold=False)
    _ensure_paragraph_style(doc, "Table Note", language, size=9, bold=False)
    _ensure_paragraph_style(doc, "References", language, size=11, bold=False)
    refs = doc.styles["References"]
    refs.paragraph_format.first_line_indent = Inches(-0.5)
    refs.paragraph_format.left_indent = Inches(0.5)
    refs.paragraph_format.space_after = Pt(6)


def _set_style_font(style, language: str, size: Optional[int] = None, bold: Optional[bool] = None, italic: Optional[bool] = None) -> None:
    font = style.font
    font.name = "SimSun" if language == "zh" else "Times New Roman"
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
    rfonts.set(qn("w:ascii"), "Times New Roman")
    rfonts.set(qn("w:hAnsi"), "Times New Roman")
    rfonts.set(qn("w:eastAsia"), "SimSun" if language == "zh" else "Times New Roman")


def _ensure_paragraph_style(doc: Document, name: str, language: str, size: int, bold: bool) -> None:
    if name not in doc.styles:
        doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    _set_style_font(doc.styles[name], language, size=size, bold=bold)


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
        cleaned = re.sub(r"(?i)https://doi\.org/", "https://doi.org/", cleaned)
        cleaned = re.sub(r"(?i)http://doi\.org/", "https://doi.org/", cleaned)
        if language == "zh":
            cleaned = cleaned.replace("Table of Contents", "目录")
            cleaned = cleaned.replace("Research Problem and Approach", "研究问题与研究方法")
            cleaned = cleaned.replace("Methodology and Findings", "研究方法与主要发现")
            cleaned = cleaned.replace("Key Contributions", "主要贡献")
            cleaned = cleaned.replace("Implications", "理论与现实意义")
            cleaned = re.sub(r"\bKeywords\s*[:：]", "关键词：", cleaned)
        cleaned = re.sub(r"\\+newpage|/newpage|\bewpage\b|\bnewpage\b", "", cleaned, flags=re.IGNORECASE)
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
            ("文稿题目", 12, False),
            (title, 20, True),
            ("文稿类型：文献综述参考稿 / Research Paper Draft", 12, False),
            (f"生成日期：{date}", 11, False),
            (f"生成工具：{generated_by}", 11, False),
            ("使用声明：本内容仅供资料梳理和写作参考，不构成正式学术成果", 10, False),
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
            run.font.name = "SimSun" if language == "zh" else "Times New Roman"
            run.font.size = Pt(size)
            run.font.bold = bold
        inserted.append(para)
    spacer = first.insert_paragraph_before("")
    spacer.paragraph_format.space_after = Pt(24)
    inserted.append(spacer)
    inserted[-1].add_run().add_break(WD_BREAK.PAGE)


def _ensure_toc(doc: Document, language: str) -> None:
    toc_title = "目录" if language == "zh" else "Table of Contents"
    toc_para = _find_toc_title_paragraph(doc)
    if toc_para:
        _replace_paragraph_text(toc_para, toc_title)
        toc_para.style = doc.styles["Heading 1"]
        toc_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if not any(_paragraph_has_toc_field(candidate) for candidate in _iter_following_paragraphs(doc, toc_para)):
            toc_field = _insert_paragraph_after(toc_para, "")
            _append_toc_field(toc_field)
        next_heading = _next_heading_after(doc, toc_para)
        if next_heading is not None:
            _ensure_page_break_before(next_heading)
        return

    insert_before = _find_first_content_paragraph(doc)
    toc_heading = insert_before.insert_paragraph_before(toc_title) if insert_before else doc.add_paragraph(toc_title)
    toc_heading.style = doc.styles["Heading 1"]
    toc_heading.alignment = WD_ALIGN_PARAGRAPH.CENTER

    toc_field = _insert_paragraph_after(toc_heading, "")
    _append_toc_field(toc_field)
    toc_field.paragraph_format.space_after = Pt(12)
    _ensure_page_break_after(toc_field)


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
    text.text = "Right-click and update field to refresh the table of contents."
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.extend([fld_begin, instr, fld_sep, text, fld_end])


def _normalize_headings(doc: Document, language: str) -> None:
    counters = [0, 0, 0, 0]
    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style else ""
        if not text:
            continue
        if _is_reference_heading(text, language):
            para.style = doc.styles["Heading 1"]
            _remove_numbering_from_paragraph(para)
            continue
        if not style.startswith("Heading"):
            continue
        level_match = re.search(r"(\d+)$", style)
        level = int(level_match.group(1)) if level_match else 1
        if level > 4:
            continue
        if _is_unnumbered_heading(text, language):
            _remove_numbering_from_paragraph(para)
            continue
        counters[level - 1] += 1
        for idx in range(level, len(counters)):
            counters[idx] = 0
        clean = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", text)
        number = ".".join(str(counters[idx]) for idx in range(level) if counters[idx])
        _replace_paragraph_text(para, f"{number}. {clean}")
        _apply_heading_numbering(para, level)


def _is_reference_heading(text: str, language: str) -> bool:
    plain = text.strip().lower()
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
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if re.match(r"^(表|Table)\s+\d+", text, re.IGNORECASE):
            caption_counter += 1
            body = re.sub(r"^(表|Table)\s+\d+[.:：]?\s*", "", text, flags=re.IGNORECASE).strip()
            new_text = f"表 {caption_counter}  {body}" if language == "zh" else f"Table {caption_counter}. {body}"
            _replace_paragraph_text(para, new_text)
            para.style = doc.styles["Caption"]
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            para.paragraph_format.keep_with_next = True
            para.paragraph_format.space_before = Pt(6)
            para.paragraph_format.space_after = Pt(3)
            caption_seen.add(caption_counter)
        elif text.startswith("注：") or text.startswith("Note:"):
            para.style = doc.styles["Table Note"]
            para.paragraph_format.space_before = Pt(3)
            para.paragraph_format.space_after = Pt(6)
    stats["captions_generated"] = len(caption_seen)


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
                        run.font.name = "SimSun" if language == "zh" else "Times New Roman"
                        run.font.size = font_size
                        run.font.bold = row_idx == 0
        stats["tables_processed"] += 1


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
    if "()" in text:
        raise ValueError("Empty math variable placeholder detected in DOCX: ()")
    if re.search(r"(?<![0-9³⁶])\sK/s\b", text):
        raise ValueError("Missing numeric value before K/s in DOCX")
    if re.search(r"(?<![A-Za-z0-9)/])\sratio\b", text):
        raise ValueError("Missing variable before ratio in DOCX")


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
