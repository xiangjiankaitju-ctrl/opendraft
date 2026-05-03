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


PAGE_BREAK_OPENXML = """```{=openxml}
<w:p><w:r><w:br w:type="page"/></w:r></w:p>
```"""


@dataclass
class DocxExportStats:
    """Telemetry from DOCX preprocessing and post-processing."""

    language: str
    tables_processed: int = 0
    markdown_tables_detected: int = 0
    docx_tables_detected: int = 0
    captions_generated: int = 0
    caption_headings_normalized: int = 0
    validation_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


def normalize_docx_language(language: Optional[str], md_content: str = "") -> str:
    """Normalize language to the DOCX template bucket."""
    lang = (language or "").strip().lower()
    if lang.startswith("zh") or lang in {"chinese", "cn", "zh-cn", "zh_cn"}:
        return "zh"
    if lang.startswith("en") or lang in {"english", ""}:
        if not lang and _looks_chinese(md_content):
            return "zh"
        return "en"
    return "en"


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
    text = _remove_duplicate_title(text, metadata.get("title"))
    text = _remove_leading_cover_residue(text, selected_language, metadata.get("title"))
    text = _normalize_doi_case(text)
    text = _normalize_math_text(text)
    _validate_math_placeholders(text)
    text = _remove_toc_placeholders(text)
    text = _remove_language_template_residue(text, selected_language)
    text = _clean_citation_braces(text)
    text = _normalize_abstract_labels(text, selected_language)
    text, caption_heading_count = _normalize_caption_headings(text, selected_language)
    stats.caption_headings_normalized = caption_heading_count
    text = _normalize_headings(text, selected_language)
    _validate_no_malformed_pipe_tables(text)
    text, caption_count = _normalize_table_captions(text, selected_language)
    stats.captions_generated = caption_count
    stats.markdown_tables_detected = _count_markdown_tables(text)
    stats.tables_processed = stats.markdown_tables_detected
    text = _convert_page_break_markers(text)
    text = _ensure_references_heading(text, selected_language)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip() + "\n"
    text = _normalize_markdown_bold_labels(text, selected_language)
    return text, stats


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
    if language == "zh":
        residue_patterns.extend([
            r"Title",
            r"Document Type",
            r"Generated by",
            r"Date",
            r"Disclaimer",
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
    ]
    for idx in range(min(len(lines), 30)):
        stripped = lines[idx].strip()
        if any(re.fullmatch(rf"{re.escape(label)}(?:\s*[:：].*)?", stripped, flags=re.IGNORECASE) for label in english_cover_lines):
            lines[idx] = ""
    return "\n".join(lines)


def _clean_citation_braces(text: str) -> str:
    text = re.sub(r"\{\s*(\([^{}\n]+?\))\s*\}", r"\1", text)
    return text


def _normalize_abstract_labels(text: str, language: str) -> str:
    if language != "zh":
        return text

    replacements = {
        "Research Problem and Approach": "研究问题与研究方法",
        "Methodology and Findings": "研究方法与主要发现",
        "Key Contributions": "主要贡献",
        "Implications": "理论与现实意义",
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
        match = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if not match:
            lines.append(line)
            continue

        hashes, heading = match.groups()
        heading = _clean_heading_text(heading, language)
        lines.append(f"{hashes} {heading}")
    return "\n".join(lines)


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

    zh_match = re.match(r"^表\s*(\d+)(?:[-‑–—]\d+)?(?:\s*[.:：])?\s*(.*)$", candidate, re.IGNORECASE)
    if zh_match:
        number, body = zh_match.groups()
        body = _clean_caption_body(body)
        return f"表 {number}" + (f"  {body}" if body else "")

    en_match = re.match(r"^Table\s*(\d+)(?:[-‑–—]\d+)?(?:\s*[.:：])?\s*(.*)$", candidate, re.IGNORECASE)
    if en_match:
        number, body = en_match.groups()
        body = _clean_caption_body(body)
        if language == "zh":
            return f"表 {number}" + (f"  {body}" if body else "")
        return f"Table {number}" + (f". {body}" if body else "")

    return None


def _clean_heading_text(heading: str, language: str) -> str:
    heading = re.sub(r"^[·•\-*]\s+", "", heading.strip())
    unnumbered_candidate = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading).strip()
    if _is_unnumbered_heading(unnumbered_candidate, language):
        return unnumbered_candidate
    if language == "zh":
        translations = {
            "introduction": "引言",
            "main body": "正文",
            "body": "正文",
            "conclusion": "结论",
            "references": "参考文献",
            "bibliography": "参考文献",
            "abstract": "摘要",
            "keywords": "关键词",
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


def _is_unnumbered_heading(heading: str, language: str) -> bool:
    plain = heading.strip().lower()
    unnumbered = {"references", "bibliography", "appendix", "appendices", "abstract"}
    if plain in unnumbered or plain.startswith("appendix "):
        return True
    if language == "zh" and heading.strip() in {"摘要", "目录", "参考文献", "附录"}:
        return True
    return False


def _normalize_table_captions(text: str, language: str) -> tuple[str, int]:
    lines = text.splitlines()
    out = []
    counter = 0
    idx = 0
    caption_pattern = re.compile(r"^\s*(?:表|Table)\s*\d+(?:[-‑–—]\d+)?(?:\s*[.:：])?\s*(.*)$", re.IGNORECASE)

    while idx < len(lines):
        line = lines[idx]
        if _is_markdown_table_start(lines, idx):
            caption_body = ""
            caption_idx = _last_nonempty_index(out)
            if caption_idx is not None and caption_pattern.match(out[caption_idx].strip()):
                raw_caption = out.pop(caption_idx).strip()
                while out and not out[-1].strip():
                    out.pop()
                caption_body = caption_pattern.match(raw_caption).group(1).strip()
            else:
                end = _markdown_table_end(lines, idx)
                if end < len(lines) and caption_pattern.match(lines[end].strip()):
                    caption_body = caption_pattern.match(lines[end].strip()).group(1).strip()
                    lines[end] = ""
            counter += 1
            out.append(_format_table_caption(counter, caption_body, language))
            out.append("")
            out.append(line)
            idx += 1
            continue

        out.append(line)
        idx += 1

    return "\n".join(out), counter


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
        return f"表 {counter}" + (f"  {caption_body}" if caption_body else "")
    return f"Table {counter}" + (f". {caption_body}" if caption_body else "")


def _clean_caption_body(caption_body: str) -> str:
    body = caption_body.strip()
    body = re.sub(r"^(?:表|Table)\s*\d+(?:[-‑–—]\d+)?(?:\s*[.:：])?\s*", "", body, flags=re.IGNORECASE).strip()
    return body


def _default_table_caption(counter: int, language: str) -> str:
    return f"表格 {counter}" if language == "zh" else f"Table {counter}"


def _count_markdown_tables(text: str) -> int:
    lines = text.splitlines()
    return sum(1 for idx in range(len(lines)) if _is_markdown_table_start(lines, idx))


def _convert_page_break_markers(text: str) -> str:
    markers = [
        r"(?im)^\s*\\\\newpage\s*$",
        r"(?im)^\s*\\newpage\s*$",
        r"(?im)^\s*/newpage\s*$",
        r"(?im)^\s*ewpage\s*$",
        r"(?im)^\s*newpage\s*$",
    ]
    for pattern in markers:
        text = re.sub(pattern, f"\n\n{PAGE_BREAK_OPENXML}\n\n", text)
    return text


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
