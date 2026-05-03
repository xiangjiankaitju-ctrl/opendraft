#!/usr/bin/env python3
"""
Create a custom reference document for Pandoc DOCX export.

This document defines the styles that Pandoc will use when converting
markdown to DOCX format. Styles include:
- Title, Subtitle, Author, Date
- Heading 1, 2, 3 (APA 7th edition compatible)
- Normal text (double-spaced, Times New Roman 12pt)
- Block quotes
- List styles
"""

from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from pathlib import Path


def create_reference_document(language: str = "en"):
    """Create a professionally styled reference document for Pandoc."""
    language = "zh" if language.lower().startswith("zh") else "en"
    doc = Document()

    # Set document margins.
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1.1 if language == "zh" else 1)
        section.right_margin = Inches(1.1 if language == "zh" else 1)

    body_font = 'Noto Serif CJK SC' if language == "zh" else 'Times New Roman'
    latin_font = 'Times New Roman'
    body_size = 12 if language == "zh" else 11

    # Configure Normal style (base for all text)
    normal = doc.styles['Normal']
    normal.font.name = body_font
    normal.font.size = Pt(body_size)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    normal.paragraph_format.line_spacing = 1.5 if language == "zh" else 1.15
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(3 if language == "zh" else 6)
    normal.paragraph_format.first_line_indent = Inches(0.29 if language == "zh" else 0)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _set_east_asia_font(normal, body_font, latin_font)

    # Configure Title style
    title = doc.styles['Title']
    title.font.name = body_font
    title.font.size = Pt(20 if language == "zh" else 18)
    title.font.bold = True
    title.font.color.rgb = RGBColor(0, 0, 0)
    title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(0)
    title.paragraph_format.space_after = Pt(12)
    title.paragraph_format.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    title.paragraph_format.first_line_indent = Inches(0)
    _set_east_asia_font(title, body_font, latin_font)

    # Configure Subtitle style
    subtitle = doc.styles['Subtitle']
    subtitle.font.name = body_font
    subtitle.font.size = Pt(14)
    subtitle.font.italic = True
    subtitle.font.color.rgb = RGBColor(0, 0, 0)
    subtitle.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.paragraph_format.space_before = Pt(0)
    subtitle.paragraph_format.space_after = Pt(12)
    subtitle.paragraph_format.first_line_indent = Inches(0)
    _set_east_asia_font(subtitle, body_font, latin_font)

    # Configure Heading 1 (Major sections - left-aligned, bold)
    h1 = doc.styles['Heading 1']
    h1.font.name = body_font
    h1.font.size = Pt(16 if language == "zh" else 14)
    h1.font.bold = True
    h1.font.color.rgb = RGBColor(0, 0, 0)
    h1.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    h1.paragraph_format.space_before = Pt(12)
    h1.paragraph_format.space_after = Pt(6)
    h1.paragraph_format.line_spacing = 1.5 if language == "zh" else 1.15
    h1.paragraph_format.first_line_indent = Inches(0)
    h1.paragraph_format.page_break_before = False
    _set_east_asia_font(h1, body_font, latin_font)

    # Configure Heading 2 (Subsections - left-aligned, bold)
    h2 = doc.styles['Heading 2']
    h2.font.name = body_font
    h2.font.size = Pt(14 if language == "zh" else 13)
    h2.font.bold = True
    h2.font.color.rgb = RGBColor(0, 0, 0)
    h2.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    h2.paragraph_format.space_before = Pt(10)
    h2.paragraph_format.space_after = Pt(4)
    h2.paragraph_format.line_spacing = 1.5 if language == "zh" else 1.15
    h2.paragraph_format.first_line_indent = Inches(0)
    _set_east_asia_font(h2, body_font, latin_font)

    # Configure Heading 3 (Sub-subsections - left-aligned, bold)
    h3 = doc.styles['Heading 3']
    h3.font.name = body_font
    h3.font.size = Pt(12)
    h3.font.bold = True
    h3.font.italic = False
    h3.font.color.rgb = RGBColor(0, 0, 0)
    h3.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    h3.paragraph_format.space_before = Pt(6)
    h3.paragraph_format.space_after = Pt(3)
    h3.paragraph_format.line_spacing = 1.5 if language == "zh" else 1.15
    h3.paragraph_format.first_line_indent = Inches(0)
    _set_east_asia_font(h3, body_font, latin_font)

    # Configure Block Text / Quote style
    try:
        block_text = doc.styles['Block Text']
    except KeyError:
        block_text = doc.styles.add_style('Block Text', WD_STYLE_TYPE.PARAGRAPH)
    block_text.font.name = body_font
    block_text.font.size = Pt(body_size)
    block_text.paragraph_format.left_indent = Inches(0.5)
    block_text.paragraph_format.right_indent = Inches(0.5)
    block_text.paragraph_format.line_spacing = 1.5 if language == "zh" else 1.15
    block_text.paragraph_format.first_line_indent = Inches(0)

    # Configure List Bullet style
    list_bullet = doc.styles['List Bullet']
    list_bullet.font.name = body_font
    list_bullet.font.size = Pt(body_size)
    list_bullet.paragraph_format.line_spacing = 1.5 if language == "zh" else 1.15
    list_bullet.paragraph_format.space_before = Pt(0)
    list_bullet.paragraph_format.space_after = Pt(0)

    # Configure List Number style
    list_number = doc.styles['List Number']
    list_number.font.name = body_font
    list_number.font.size = Pt(body_size)
    list_number.paragraph_format.line_spacing = 1.5 if language == "zh" else 1.15
    list_number.paragraph_format.space_before = Pt(0)
    list_number.paragraph_format.space_after = Pt(0)

    _configure_paragraph_style(doc, 'Caption', body_font, latin_font, 10, False, 0, 3, 1.15, WD_ALIGN_PARAGRAPH.LEFT)
    _configure_paragraph_style(doc, 'Table Note', body_font, latin_font, 9, False, 3, 6, 1.15, WD_ALIGN_PARAGRAPH.LEFT)
    _configure_paragraph_style(doc, 'References', body_font, latin_font, body_size, False, 0, 3 if language == "zh" else 6, 1.5 if language == "zh" else 1.15, WD_ALIGN_PARAGRAPH.LEFT)
    _configure_paragraph_style(doc, 'Abstract Label', body_font, latin_font, body_size, True, 0, 3, 1.5 if language == "zh" else 1.15, WD_ALIGN_PARAGRAPH.LEFT)
    _configure_paragraph_style(doc, 'Keywords', body_font, latin_font, body_size, False, 0, 3, 1.5 if language == "zh" else 1.15, WD_ALIGN_PARAGRAPH.LEFT)

    # Add sample content to demonstrate styles (Pandoc needs at least one use of each style)
    doc.add_paragraph('文稿题目' if language == "zh" else 'Title', style='Title')
    doc.add_paragraph('副标题' if language == "zh" else 'Subtitle', style='Subtitle')
    doc.add_paragraph('一级标题' if language == "zh" else 'Heading 1', style='Heading 1')
    doc.add_paragraph('二级标题' if language == "zh" else 'Heading 2', style='Heading 2')
    doc.add_paragraph('三级标题' if language == "zh" else 'Heading 3', style='Heading 3')
    doc.add_paragraph('正文段落示例。' if language == "zh" else 'Normal paragraph text with proper academic formatting.', style='Normal')
    doc.add_paragraph('表 1  示例表题' if language == "zh" else 'Table 1. Example caption', style='Caption')
    doc.add_paragraph('注：示例表注。' if language == "zh" else 'Note: Example table note.', style='Table Note')
    doc.add_paragraph('关键词：示例；模板' if language == "zh" else 'Keywords: example; template', style='Keywords')
    doc.add_paragraph('Block quote text.', style='Block Text')
    doc.add_paragraph('Bullet item', style='List Bullet')
    doc.add_paragraph('Numbered item', style='List Number')

    # Save the reference document
    output_dir = Path(__file__).parent.parent / 'templates'
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / ('zh_reference.docx' if language == "zh" else 'en_reference.docx')
    doc.save(str(output_path))
    print(f"Created reference document: {output_path}")
    return output_path


def _set_east_asia_font(style, east_asia_font: str, latin_font: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), latin_font)
    rfonts.set(qn("w:hAnsi"), latin_font)
    rfonts.set(qn("w:eastAsia"), east_asia_font)


def _configure_paragraph_style(
    doc: Document,
    name: str,
    east_asia_font: str,
    latin_font: str,
    size: float,
    bold: bool,
    before: float,
    after: float,
    line_spacing: float,
    alignment,
) -> None:
    try:
        style = doc.styles[name]
    except KeyError:
        style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    style.font.name = east_asia_font
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor(0, 0, 0)
    style.paragraph_format.alignment = alignment
    style.paragraph_format.space_before = Pt(before)
    style.paragraph_format.space_after = Pt(after)
    style.paragraph_format.line_spacing = line_spacing
    style.paragraph_format.first_line_indent = Inches(0)
    _set_east_asia_font(style, east_asia_font, latin_font)


if __name__ == '__main__':
    create_reference_document("en")
    create_reference_document("zh")
