#!/usr/bin/env python3
"""
Production DOCX export preprocessing helpers.

The functions in this module prepare markdown for Pandoc DOCX export without
changing the upstream draft generation logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Optional

try:
    import yaml
except ImportError:  # pragma: no cover - exercised in minimal runtime environments
    yaml = None

from utils.document_ast import normalize_document_markdown, normalize_language_code
from utils.final_artifact_contract import (
    PAGEBREAK_MARKER,
    clean_citation_residuals,
    detect_damaged_technical_tokens,
    find_technical_tokens,
    normalize_pagebreaks,
)


PAGE_BREAK_OPENXML = """```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```"""


@dataclass
class DocxExportStats:
    """Telemetry from DOCX preprocessing and post-processing."""

    language: str
    tables_processed: int = 0
    markdown_tables_detected: int = 0
    markdown_table_column_counts: list[int] = field(default_factory=list)
    docx_tables_detected: int = 0
    captions_generated: int = 0
    caption_headings_normalized: int = 0
    citation_residue_repaired: bool = False
    duplicate_pagebreak_repaired: bool = False
    technical_tokens_protected: bool = False
    damaged_tokens: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    language_residuals_fixed: list[str] = field(default_factory=list)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


def normalize_docx_language(language: Optional[str], md_content: str = "") -> str:
    """Normalize language to the DOCX template bucket."""
    if not str(language or "").strip() and _looks_chinese(md_content):
        return "zh"
    normalized = normalize_language_code(language)
    return normalized if normalized in {"zh", "en"} else "en"


def extract_markdown_metadata(md_content: str) -> dict:
    """Extract YAML front matter from markdown content."""
    if not md_content.lstrip().startswith("---"):
        return {}

    parts = md_content.split("---", 2)
    if len(parts) < 3:
        return {}

    if yaml is None:
        return _parse_simple_front_matter(parts[1])

    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}

    return data if isinstance(data, dict) else {}


def select_reference_template(language: str) -> Path:
    """Return the repository-packaged DOCX reference template path."""
    template_name = "zh_reference.docx" if language == "zh" else "en_reference.docx"
    return Path(__file__).resolve().parent.parent / "templates" / template_name


def preprocess_markdown_for_docx(md_content: str, language: Optional[str] = None) -> tuple[str, DocxExportStats]:
    """Prepare markdown for production DOCX export."""
    metadata = extract_markdown_metadata(md_content)
    selected_language = normalize_docx_language(
        language or metadata.get("language") or metadata.get("lang"),
        md_content,
    )
    stats = DocxExportStats(language=selected_language)

    text = _strip_front_matter(md_content)
    original_body_for_tokens = text
    text = _remove_duplicate_title(text, metadata.get("title"))
    text = _remove_leading_cover_residue(text, selected_language, metadata.get("title"))
    text = _normalize_doi_case(text)
    text = _normalize_math_text(text)
    text = _normalize_symbolic_text(text)
    before_tokens = text
    text = protect_technical_tokens_for_markdown(text)
    stats.technical_tokens_protected = text != before_tokens or bool(_technical_token_presence(original_body_for_tokens))
    _validate_math_placeholders(text)
    text = _remove_toc_placeholders(text)
    text = _remove_language_template_residue(text, selected_language)
    text, residual_fixes = clean_language_residuals(text, selected_language)
    stats.language_residuals_fixed.extend(residual_fixes)
    before_citation_cleanup = text
    text = _clean_citation_braces(text)
    text = _normalize_citation_parentheses(text, selected_language)
    text = _remove_raw_citation_tokens(text)
    if selected_language == "zh":
        text = _normalize_zh_citation_spacing(text)
    stats.citation_residue_repaired = text != before_citation_cleanup
    text = _normalize_abstract_labels(text, selected_language)
    text, caption_heading_count = _normalize_caption_headings(text, selected_language)
    stats.caption_headings_normalized = caption_heading_count
    text = _normalize_headings(text, selected_language)
    text, structure = normalize_document_markdown(text, selected_language)
    stats.warnings.extend(item.get("message", str(item)) for item in structure.warnings)
    _validate_no_malformed_pipe_tables(text)
    text, caption_count, table_number_map = _normalize_table_captions(text, selected_language)
    stats.captions_generated = caption_count
    text = _normalize_table_reference_numbers(text, selected_language, caption_count, table_number_map)
    _validate_table_references(text, selected_language, caption_count, stats)
    stats.markdown_tables_detected = _count_markdown_tables(text)
    stats.markdown_table_column_counts = _markdown_table_column_counts(text)
    stats.tables_processed = stats.markdown_tables_detected
    before_pagebreak_cleanup = text
    text = _collapse_duplicate_pagebreaks(text)
    stats.duplicate_pagebreak_repaired = text != before_pagebreak_cleanup
    text = _convert_page_break_markers(text)
    text = _ensure_references_heading(text, selected_language)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip() + "\n"
    text = _normalize_markdown_bold_labels(text, selected_language)
    stats.damaged_tokens = _detect_damaged_technical_tokens(original_body_for_tokens, text)
    if stats.damaged_tokens:
        stats.warnings.append(f"Technical tokens may have been damaged before DOCX export: {stats.damaged_tokens}")
    return text, stats


def clean_language_residuals(text: str, language: str) -> tuple[str, list[str]]:
    """Repair fixed mixed-language residues outside references/code blocks."""
    language = normalize_docx_language(language, text)
    fixed: list[str] = []
    replacements = (
        {
            "Table of Contents": "目录",
            "Abstract": "摘要",
            "Keywords": "关键词",
            "References": "参考文献",
            "Generated by": "",
            "Title": "",
            "Disclaimer": "",
            "Research Problem and Approach": "研究问题与方法",
            "Methodology and Findings": "研究方法与主要发现",
            "Key Contributions": "主要贡献",
            "Implications": "理论与实践意义",
        }
        if language == "zh"
        else {
            "目录": "Table of Contents",
            "摘要": "Abstract",
            "关键词": "Keywords",
            "参考文献": "References",
            "使用声明": "",
            "生成工具": "",
        }
    )

    lines = text.splitlines()
    out: list[str] = []
    in_code = False
    in_references = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if re.match(r"^\s*#{1,6}\s*(?:\d+\.\s*)?(?:参考文献|References|Bibliography)\s*$", line, flags=re.IGNORECASE):
            in_references = True
        if in_code or in_references or re.search(r"https?://|doi\.org/|DOI\b", line, flags=re.IGNORECASE):
            out.append(line)
            continue

        cleaned = line
        for source, target in replacements.items():
            if re.search(re.escape(source), cleaned, flags=re.IGNORECASE):
                cleaned = re.sub(re.escape(source), target, cleaned, flags=re.IGNORECASE)
                fixed.append(source)
        if language == "zh":
            cleaned = re.sub(r"\bsection\s+\d+(?:\.\d+)*\b", "相关章节", cleaned, flags=re.IGNORECASE)
        if cleaned != line:
            fixed.append("section_or_phrase_residual")
        out.append(cleaned)

    return "\n".join(out), sorted(set(fixed))


def _looks_chinese(text: str) -> bool:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    non_space = len(re.findall(r"\S", text or ""))
    return cjk >= 20 and (cjk / max(non_space, 1)) >= 0.15


def _strip_front_matter(md_content: str) -> str:
    """Remove YAML front matter so Pandoc cannot render a second title block."""
    if not md_content.lstrip().startswith("---"):
        return md_content

    parts = md_content.split("---", 2)
    if len(parts) < 3:
        return md_content
    return parts[2].lstrip("\n")


def _remove_duplicate_title(text: str, title: Optional[str]) -> str:
    """Drop leading body titles once they have been promoted to the DOCX cover."""
    if not title:
        return text
    normalized_title = _normalize_title_for_compare(str(title))
    lines = text.splitlines()
    for idx in range(min(len(lines), 30)):
        candidate = re.sub(r"^\s*#{1,6}\s*", "", lines[idx]).strip()
        if candidate and _normalize_title_for_compare(candidate) == normalized_title:
            lines[idx] = ""
    return "\n".join(lines)


def _normalize_title_for_compare(text: str) -> str:
    text = re.sub(r"[*_`#：:，,\s]+", "", text.strip().lower())
    return text


def _remove_leading_cover_residue(text: str, language: str, title: Optional[str]) -> str:
    """Remove legacy cover-template lines only from the document front."""
    lines = text.splitlines()
    residue_patterns = [
        r"OpenDraft AI",
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}",
        r"OPENDRAFT UNIVERSITY",
        r"Faculty of Engineering",
        r"Department of Computer Science",
        r"Research Paper",
    ]
    residue_patterns.extend([
        r"Title",
        r"题目\s*[:：]?",
        r"Document Type",
        r"Generated by(?:\s*[:：].*)?",
        r"生成工具(?:\s*[:：].*)?",
        r"Date",
        r"Disclaimer",
        r"This content is for research organization and writing reference only\.",
        r"使用声明\s*[:：]?.*",
        r"OpenDraft AI\s*-?\s*https://github\.com/federicodeponte/opendraft",
    ])
    normalized_title = _normalize_title_for_compare(str(title)) if title else ""
    for idx in range(min(len(lines), 30)):
        stripped = re.sub(r"^\s*#{1,6}\s*", "", lines[idx]).strip()
        if not stripped:
            continue
        if normalized_title and _normalize_title_for_compare(stripped) == normalized_title:
            lines[idx] = ""
            continue
        if any(re.fullmatch(pattern, stripped, flags=re.IGNORECASE) for pattern in residue_patterns):
            lines[idx] = ""
    return "\n".join(lines)


def _parse_simple_front_matter(front_matter: str) -> dict:
    data = {}
    for line in front_matter.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("'\"")
    return data


def _normalize_doi_case(text: str) -> str:
    return re.sub(r"https?://doi\.org/", "https://doi.org/", text, flags=re.IGNORECASE)


def protect_technical_tokens_for_markdown(text: str) -> str:
    """Escape Markdown-sensitive technical tokens before Pandoc parses them."""
    text = re.sub(r"(?<![A-Za-z0-9\\])(D)\*(\s+Lite\b)", r"\1\\*\2", text)
    text = re.sub(r"(?<![A-Za-z0-9\\])(CCD|D|A)\*(?![A-Za-z0-9])", r"\1\\*", text)
    return text


def _normalize_symbolic_text(text: str) -> str:
    lines: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            lines.append(line)
            continue
        if in_code or re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", line):
            lines.append(line)
            continue
        if "PAGEBREAK" in line:
            lines.append(normalize_pagebreaks(line))
            continue
        fixed = re.sub(r"—-|-—", "——", line)
        fixed = re.sub(r"-{2,}", "——", fixed)
        lines.append(fixed)
    return "\n".join(lines)


def _technical_token_presence(text: str) -> set[str]:
    return set(find_technical_tokens(text))


def _detect_damaged_technical_tokens(source: str, output: str) -> list[str]:
    return detect_damaged_technical_tokens(source, output)


def _normalize_math_text(text: str) -> str:
    replacements = {
        r"$10^3$": "10³",
        r"$10^{3}$": "10³",
        r"$10^6$": "10⁶",
        r"$10^{6}$": "10⁶",
        r"$G$": "G",
        r"$R$": "R",
        r"$G/R$": "G/R",
        r"$G \times R$": "G × R",
        r"$G\\times R$": "G × R",
        r"$G \\times R$": "G × R",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _validate_math_placeholders(text: str) -> None:
    """Fail fast when the draft contains visible math-loss placeholders."""
    checks = [
        (r"\bthermal gradients\s*\(\)", "Empty thermal gradient placeholder detected: thermal gradients ()"),
        (r"\bgrowth velocity\s*\(\)", "Empty growth velocity placeholder detected: growth velocity ()"),
        (r"\bcooling rates exceeding\s+K/s\b", "Missing numeric value before K/s"),
        (r"\bresulting\s+ratio\b", "Missing variable before ratio"),
    ]
    for pattern, message in checks:
        if re.search(pattern, text):
            raise ValueError(message)


def _remove_toc_placeholders(text: str) -> str:
    patterns = [
        r"(?im)^\s*#{1,6}\s*(Table of Contents|目录)\s*$",
        r"(?im)^\s*(Table of Contents|目录)\s*$",
        r"(?im)^\s*Right-click and update field to refresh the table of contents\.\s*$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text)
    return text


def _remove_language_template_residue(text: str, language: str) -> str:
    if language != "zh":
        return text
    lines = text.splitlines()
    english_cover_lines = [
        "Title",
        "Document Type",
        "Generated by",
        "Date",
        "Disclaimer",
        "This content is for research organization and writing reference only.",
    ]
    for idx in range(min(len(lines), 30)):
        stripped = lines[idx].strip()
        if any(re.fullmatch(rf"{re.escape(label)}(?:\s*[:：].*)?", stripped, flags=re.IGNORECASE) for label in english_cover_lines):
            lines[idx] = ""
    return "\n".join(lines)


def _clean_citation_braces(text: str) -> str:
    return clean_citation_residuals(text, "en")


def _normalize_citation_parentheses(text: str, language: str) -> str:
    return clean_citation_residuals(text, language)


def _remove_raw_citation_tokens(text: str) -> str:
    text = re.sub(r"\{\{\s*cite_\d{3,}\s*\}\}", "", text)
    text = re.sub(r"\{\s*cite_\d{3,}\s*\}", "", text)
    text = re.sub(r"\bcite_\d{3,}\b", "", text)
    return text


def _normalize_zh_citation_spacing(text: str) -> str:
    text = re.sub(r"([\u4e00-\u9fff])\s+（", r"\1（", text)
    text = re.sub(r"）\s+([。；，、])", r"）\1", text)
    text = re.sub(r"\s{2,}（", "（", text)
    return text


def _normalize_abstract_labels(text: str, language: str) -> str:
    if language != "zh":
        return text

    replacements = {
        "Research Problem and Approach": "研究问题与方法",
        "Methodology and Findings": "研究方法与主要发现",
        "Key Contributions": "主要贡献",
        "Implications": "理论与实践意义",
        "Keywords": "关键词",
        "Abstract": "摘要",
    }
    for source, target in replacements.items():
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    text = re.sub(r"(?im)^(#{1,6})\s+摘要\s*$", r"\1 摘要", text)
    text = re.sub(r"(?im)^\*\*关键词:\*\*", "关键词：", text)
    text = re.sub(r"(?im)^\*\*关键词：\*\*", "关键词：", text)
    return text


def _normalize_headings(text: str, language: str) -> str:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^(\s*#{0,6}\s*)[·•]\s+(?=\d+(?:\.\d+)*\.?\s+)", r"\1", line)
        line = re.sub(r"^(\s*#{0,6}\s*)[-*]\s+(?=\d+(?:\.\d+)*\.?\s+)", r"\1", line)
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            lines.append(line)
            continue

        hashes, heading = match.groups()
        heading = _clean_heading_text(heading, language)
        lines.append(f"{hashes} {heading}")
    return "\n".join(lines)


def _normalize_heading_depth_and_numbering(text: str, language: str) -> str:
    counters = [0, 0, 0]
    last_parent: dict[int, tuple[int, ...]] = {}
    current_top = ""
    in_intro = False
    in_references = False
    out: list[str] = []

    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            out.append(line)
            continue
        hashes, heading = match.groups()
        raw_depth = len(hashes)
        plain = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading.strip()).strip()
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

        top_match = re.match(r"^(\d+)(?:\.\d+)*\.?\s+", heading.strip())
        top_number = top_match.group(1) if top_match else ""
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
                counters = [int(top_number), 0, 0] if top_number else [counters[0] + 1, 0, 0]
                number = str(counters[0])
                current_top = number
                in_intro = number == "1" or plain_key in {"introduction", "引言"}
            else:
                existing_parts = [int(part) for part in re.findall(r"\d+", heading.strip().split()[0])]
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


def _normalize_caption_headings(text: str, language: str) -> tuple[str, int]:
    """Convert heading-like table captions into ordinary caption paragraphs."""
    lines = []
    normalized_count = 0
    for line in text.splitlines():
        match = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if not match:
            lines.append(line)
            continue

        caption = _extract_table_caption(match.group(1), language)
        if caption is None:
            lines.append(line)
            continue

        lines.append(caption)
        normalized_count += 1

    return "\n".join(lines), normalized_count


def _extract_table_caption(text: str, language: str) -> Optional[str]:
    candidate = text.strip()
    candidate = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", candidate).strip()

    zh_match = re.match(r"^表\s*(\d+(?:[.\-‑–—]\d+)?)(?:\s*[.:：])?\s*(.*)$", candidate, re.IGNORECASE)
    if zh_match:
        number, body = zh_match.groups()
        body = _clean_caption_body(body)
        return f"表 {number}" + (f"  {body}" if body else "")

    en_match = re.match(r"^Table\s*(\d+(?:[.\-‑–—]\d+)?)(?:\s*[.:：])?\s*(.*)$", candidate, re.IGNORECASE)
    if en_match:
        number, body = en_match.groups()
        body = _clean_caption_body(body)
        if language == "zh":
            return f"表 {number}" + (f"  {body}" if body else "")
        return f"Table {number}" + (f". {body}" if body else "")

    return None


def _clean_heading_text(heading: str, language: str) -> str:
    heading = re.sub(r"^[·•\-*]\s+", "", heading.strip())
    if language == "zh":
        heading = re.sub(r"^(\d+(?:\.\d+)*\.?\s+)[一二三四五六七八九十]+[、.．]\s*", r"\1", heading)
    unnumbered_candidate = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading).strip()
    if _is_unnumbered_heading(unnumbered_candidate, language):
        return unnumbered_candidate
    if language == "zh":
        translations = {
            "introduction": "引言",
            "main body": "文献综述",
            "body": "文献综述",
            "正文": "文献综述",
            "引言": "引言",
            "literature review": "文献综述",
            "文献综述": "文献综述",
            "methodology": "研究方法",
            "methods": "研究方法",
            "研究方法": "研究方法",
            "analysis": "分析结果",
            "results": "分析结果",
            "analysis and results": "分析结果",
            "分析结果": "分析结果",
            "discussion": "讨论",
            "讨论": "讨论",
            "conclusion": "结论",
            "结论": "结论",
            "references": "参考文献",
            "bibliography": "参考文献",
            "abstract": "摘要",
            "keywords": "关键词",
        }
    else:
        translations = {
            "main body": "Literature Review",
            "body": "Literature Review",
        }
        numbered = re.match(r"^(\d+(?:\.\d+)*\.?)\s+(.+)$", heading)
        if numbered:
            prefix, body = numbered.groups()
            translated = translations.get(body.strip().lower())
            if translated:
                return f"{prefix} {translated}"
        translated = translations.get(heading.strip().lower())
        if translated:
            return translated
        return heading
    if language == "zh":
        numbered = re.match(r"^(\d+(?:\.\d+)*\.?)\s+(.+)$", heading)
        if numbered:
            prefix, body = numbered.groups()
            translated = translations.get(body.strip().lower())
            if translated:
                return f"{prefix} {translated}"
        translated = translations.get(heading.strip().lower())
        if translated:
            return translated
    return heading


def _is_unnumbered_heading(heading: str, language: str) -> bool:
    plain = heading.strip().lower()
    unnumbered = {"references", "bibliography", "appendix", "appendices", "abstract"}
    if plain in unnumbered or plain.startswith("appendix "):
        return True
    if language == "zh" and heading.strip() in {"摘要", "目录", "参考文献", "附录"}:
        return True
    return False


def _normalize_table_captions(text: str, language: str) -> tuple[str, int, dict[str, str]]:
    lines = text.splitlines()
    out = []
    counter = 0
    number_map: dict[str, str] = {}
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        if _is_markdown_table_start(lines, idx):
            caption_idx = _last_nonempty_index(out)
            if caption_idx is not None and _is_true_table_caption(out[caption_idx].strip(), language):
                while len(out) - 1 > caption_idx and not out[-1].strip():
                    out.pop()
                raw_caption = out.pop(caption_idx).strip()
                counter += 1
                _record_table_number_mapping(number_map, raw_caption, counter, language)
                out.append(_format_table_caption(counter, _caption_body(raw_caption), language))
                out.append("")
            else:
                end = _markdown_table_end(lines, idx)
                if end < len(lines) and _is_true_table_caption(lines[end].strip(), language):
                    counter += 1
                    _record_table_number_mapping(number_map, lines[end].strip(), counter, language)
                    lines[end] = _format_table_caption(counter, _caption_body(lines[end].strip()), language)
            out.append(line)
            idx += 1
            continue

        out.append(line)
        idx += 1

    return "\n".join(out), counter, number_map


def _is_true_table_caption(line: str, language: str) -> bool:
    """Return True for actual captions, not prose like '表 2 总结了...'."""
    stripped = line.strip().strip("*_")
    match = re.match(
        r"^(?:表|Table)\s*\d+(?:[.\-‑–—]\d+)?(?:(?P<punct>[.:：])|\s{2,}|\s+)(?P<body>.*)$",
        stripped,
        re.IGNORECASE,
    )
    if not match:
        return False
    body = (match.group("body") or "").strip()
    if not body:
        return True
    if match.group("punct"):
        return True
    if language == "zh" and re.match(r"^(?:总结|对比|归纳|显示|说明|表明|展示|列出|给出|呈现)了?", body):
        return False
    if language != "zh" and re.match(r"^(?:shows?|summari[sz]es|compares?|lists?|presents?|indicates?)\b", body, re.IGNORECASE):
        return False
    return True


def _caption_body(line: str) -> str:
    stripped = line.strip().strip("*_")
    return re.sub(
        r"^(?:表|Table)\s*\d+(?:[.\-‑–—]\d+)?(?:[.:：]|\s{2,}|\s+)?\s*",
        "",
        stripped,
        flags=re.IGNORECASE,
    ).strip()


def _validate_no_malformed_pipe_tables(text: str) -> None:
    lines = text.splitlines()
    bad_caption_table = re.compile(r"^\s*(?:表|Table)\s*\d+.*\|.*\|", re.IGNORECASE)
    for idx, line in enumerate(lines):
        if not bad_caption_table.match(line):
            continue
        next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        if re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", next_line):
            continue
        raise ValueError(
            "Malformed markdown table: table caption and pipe cells appear on the same line. "
            "Use a caption line followed by a standard pipe table."
        )


def _last_nonempty_index(lines: list[str]) -> Optional[int]:
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].strip():
            return idx
    return None


def _markdown_table_end(lines: list[str], idx: int) -> int:
    end = idx
    while end < len(lines) and "|" in lines[end]:
        end += 1
    while end < len(lines) and not lines[end].strip():
        end += 1
    return end


def _is_markdown_table_start(lines: list[str], idx: int) -> bool:
    if idx + 1 >= len(lines):
        return False
    return "|" in lines[idx] and re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", lines[idx + 1]) is not None


def _format_table_caption(counter: int, caption_body: str, language: str) -> str:
    caption_body = _clean_caption_body(caption_body)
    if language == "zh":
        return f"表{counter}" + (f"：{caption_body}" if caption_body else "")
    return f"Table {counter}" + (f". {caption_body}" if caption_body else "")


def _clean_caption_body(caption_body: str) -> str:
    body = caption_body.strip()
    body = re.sub(r"^(?:表|Table)\s*\d+(?:[.\-‑–—]\d+)?(?:\s*[.:：])?\s*", "", body, flags=re.IGNORECASE).strip()
    body = re.sub(r"^[.:：]\s*", "", body)
    return body


def _record_table_number_mapping(number_map: dict[str, str], caption: str, counter: int, language: str) -> None:
    pattern = r"表\s*\d+(?:[.\-‑–—]\d+)?" if language == "zh" else r"Table\s+\d+(?:[.\-‑–—]\d+)?"
    match = re.match(pattern, caption.strip().strip("*_"), flags=re.IGNORECASE)
    if match:
        key = re.sub(r"\s+", "", match.group(0).lower())
        number_map.setdefault(key, str(counter))


def _default_table_caption(counter: int, language: str) -> str:
    return f"表{counter}" if language == "zh" else f"Table {counter}"


def _count_markdown_tables(text: str) -> int:
    lines = text.splitlines()
    return sum(1 for idx in range(len(lines)) if _is_markdown_table_start(lines, idx))


def _markdown_table_column_counts(text: str) -> list[int]:
    lines = text.splitlines()
    counts: list[int] = []
    for idx, line in enumerate(lines):
        if _is_markdown_table_start(lines, idx):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            counts.append(len(cells))
    return counts


def _convert_page_break_markers(text: str) -> str:
    text = normalize_pagebreaks(text)
    return re.sub(
        r"(?im)^\s*" + re.escape(PAGEBREAK_MARKER) + r"\s*$",
        f"\n\n{PAGE_BREAK_OPENXML}\n\n",
        text,
    )


def _collapse_duplicate_pagebreaks(text: str) -> str:
    return normalize_pagebreaks(text)


def _ensure_references_heading(text: str, language: str) -> str:
    if language != "zh":
        return text
    text = re.sub(r"(?im)^(#{1,6})\s*(?:\d+\.\s*)?References\s*$", r"\1 参考文献", text)
    text = re.sub(r"(?im)^(#{1,6})\s*(?:\d+\.\s*)?Bibliography\s*$", r"\1 参考文献", text)
    return text


def _normalize_markdown_bold_labels(text: str, language: str) -> str:
    if language == "zh":
        text = re.sub(r"(?im)^\*\*(摘要|关键词)\s*[:：]?\*\*", r"**\1**", text)
    else:
        text = re.sub(r"(?im)^\*\*(Abstract|Keywords)\s*[:：]?\*\*", r"**\1**", text)
    return text


def _validate_table_references(
    text: str,
    language: str,
    caption_count: int,
    stats: Optional[DocxExportStats] = None,
) -> None:
    if caption_count <= 0:
        return
    if language == "zh":
        refs = [int(num) for num in re.findall(r"表\s*(\d+)", text)]
    else:
        refs = [int(num) for num in re.findall(r"\bTable\s+(\d+)\b", text, flags=re.IGNORECASE)]
    invalid = sorted({num for num in refs if num < 1 or num > caption_count})
    if invalid:
        label = "表" if language == "zh" else "Table"
        message = f"{label} reference number does not match final captions: {invalid} > {caption_count}"
        if stats is not None:
            stats.warnings.append(message)
            return
        raise ValueError(message)


def _normalize_table_reference_numbers(
    text: str,
    language: str,
    caption_count: int,
    number_map: Optional[dict[str, str]] = None,
) -> str:
    if caption_count <= 0:
        return text

    number_map = number_map or {}

    def resolve(label_text: str) -> str:
        key = re.sub(r"\s+", "", label_text.lower())
        mapped = number_map.get(key)
        if mapped:
            return mapped
        num_match = re.search(r"\d+", label_text)
        if not num_match:
            return "1"
        num = int(num_match.group(0))
        if num < 1:
            return "1"
        if num > caption_count:
            return str(caption_count)
        return str(num)

    out: list[str] = []
    for line in text.splitlines():
        if _is_true_table_caption(line.strip(), language):
            out.append(line)
            continue
        if language == "zh":
            out.append(
                re.sub(
                    r"表\s*\d+(?:[.\-‑–—]\d+)?",
                    lambda match: f"表{resolve(match.group(0))}",
                    line,
                )
            )
        else:
            out.append(
                re.sub(
                    r"\bTable\s+\d+(?:[.\-‑–—]\d+)?\b",
                    lambda match: f"Table {resolve(match.group(0))}",
                    line,
                    flags=re.IGNORECASE,
                )
            )
    return "\n".join(out)
