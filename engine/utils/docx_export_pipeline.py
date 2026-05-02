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
    captions_generated: int = 0
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

    text = _normalize_front_matter(md_content, selected_language)
    text = _normalize_doi_case(text)
    text = _normalize_math_text(text)
    _validate_math_placeholders(text)
    text = _remove_toc_placeholders(text)
    text = _normalize_abstract_labels(text, selected_language)
    text = _normalize_headings(text, selected_language)
    text, caption_count = _normalize_table_captions(text, selected_language)
    stats.captions_generated = caption_count
    stats.tables_processed = _count_markdown_tables(text)
    text = _convert_page_break_markers(text)
    text = _ensure_references_heading(text, selected_language)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip() + "\n"
    return text, stats


def _looks_chinese(text: str) -> bool:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return cjk >= 20 and cjk >= latin


def _normalize_front_matter(md_content: str, language: str) -> str:
    """Normalize common front matter fields while preserving values."""
    if not md_content.lstrip().startswith("---"):
        return md_content

    parts = md_content.split("---", 2)
    if len(parts) < 3:
        return md_content

    if yaml is None:
        data = _parse_simple_front_matter(parts[1])
    else:
        try:
            data = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            return md_content

    if not isinstance(data, dict):
        return md_content

    normalized = {}
    field_map = {
        "titel": "title",
        "título": "title",
        "titre": "title",
        "langue": "language",
        "sprache": "language",
        "idioma": "language",
        "generated_by": "system_credit",
    }
    for key, value in data.items():
        normalized[field_map.get(str(key).lower(), str(key).lower())] = value
    normalized["language"] = language

    if yaml is None:
        yaml_text = "\n".join(f"{key}: {value}" for key, value in normalized.items())
    else:
        yaml_text = yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{yaml_text}\n---{parts[2]}"


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
        (r"\(\)", "Empty math variable placeholder detected: ()"),
        (r"(?<![0-9³⁶])\sK/s\b", "Missing numeric value before K/s"),
        (r"(?<![A-Za-z0-9)/])\sratio\b", "Missing variable before ratio"),
    ]
    for pattern, message in checks:
        if re.search(pattern, text):
            raise ValueError(message)


def _remove_toc_placeholders(text: str) -> str:
    patterns = [
        r"(?im)^\s*#{1,6}\s*(Table of Contents|目录)\s*$",
        r"(?im)^\s*(Table of Contents|目录)\s*$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text)
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
    counters = [0, 0, 0, 0]
    lines = []
    for line in text.splitlines():
        match = re.match(r"^(#{1,4})\s+(.+?)\s*$", line)
        if not match:
            lines.append(line)
            continue

        hashes, heading = match.groups()
        level = len(hashes)
        heading = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", heading).strip()

        if _is_unnumbered_heading(heading, language):
            lines.append(f"{hashes} {heading}")
            continue

        counters[level - 1] += 1
        for idx in range(level, len(counters)):
            counters[idx] = 0
        number = ".".join(str(counters[idx]) for idx in range(level) if counters[idx])
        lines.append(f"{hashes} {number}. {heading}")

    return "\n".join(lines)


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
    caption_pattern = re.compile(r"^\s*(?:表|Table)\s*\d+(?:[.:：])?\s*(.*)$", re.IGNORECASE)

    while idx < len(lines):
        line = lines[idx]
        if _is_markdown_table_start(lines, idx):
            if out and caption_pattern.match(out[-1].strip()):
                raw_caption = out.pop().strip()
                caption_body = caption_pattern.match(raw_caption).group(1).strip()
            else:
                caption_body = _default_table_caption(counter + 1, language)
            counter += 1
            out.append(_format_table_caption(counter, caption_body, language))
            out.append(line)
            idx += 1
            continue

        if caption_pattern.match(line.strip()):
            idx += 1
            continue

        out.append(line)
        idx += 1

    return "\n".join(out), counter


def _is_markdown_table_start(lines: list[str], idx: int) -> bool:
    if idx + 1 >= len(lines):
        return False
    return "|" in lines[idx] and re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", lines[idx + 1]) is not None


def _format_table_caption(counter: int, caption_body: str, language: str) -> str:
    if language == "zh":
        body = caption_body or _default_table_caption(counter, language)
        return f"表 {counter}  {body}"
    body = caption_body or _default_table_caption(counter, language)
    return f"Table {counter}. {body}"


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
