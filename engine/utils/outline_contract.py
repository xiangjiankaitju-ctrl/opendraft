#!/usr/bin/env python3
"""Outline extraction and integrity checks for frozen Markdown/DOCX structure."""

from __future__ import annotations

from pathlib import Path
import json
import re
from typing import Any, Iterable


def extract_markdown_outline(content: str, *, source_stage: str = "") -> list[dict[str, Any]]:
    """Return the structural numbered heading outline from Markdown."""
    body = _strip_front_matter(content or "")
    outline: list[dict[str, Any]] = []
    in_code = False
    for line_no, line in enumerate(body.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        raw_heading = match.group(2).strip()
        number, title = split_outline_heading(raw_heading)
        if not number:
            continue
        if _is_system_heading(title):
            continue
        if _is_caption_heading(title, number):
            continue
        outline.append(
            {
                "level": min(len(match.group(1)), 3),
                "number": number,
                "title": title,
                "text": f"{number} {title}".strip(),
                "line_no": line_no,
                "source_stage": source_stage,
            }
        )
    return outline


def extract_docx_outline(docx_path: Path, *, source_stage: str = "") -> list[dict[str, Any]]:
    from docx import Document

    outline: list[dict[str, Any]] = []
    doc = Document(docx_path)
    for idx, para in enumerate(doc.paragraphs, start=1):
        text = para.text.strip()
        style = para.style.name if para.style else ""
        if style not in {"Heading 1", "Heading 2", "Heading 3"}:
            continue
        number, title = split_outline_heading(text)
        if not number:
            continue
        if _is_system_heading(title):
            continue
        if _is_caption_heading(title, number):
            continue
        outline.append(
            {
                "level": int(style.rsplit(" ", 1)[-1]),
                "number": number,
                "title": title,
                "text": f"{number} {title}".strip(),
                "paragraph_index": idx,
                "source_stage": source_stage,
            }
        )
    return outline


def split_outline_heading(heading: str) -> tuple[str, str]:
    clean = re.sub(r"^[·•\-*]\s+", "", (heading or "").strip())
    match = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$", clean)
    if not match:
        return "", clean
    return match.group(1), match.group(2).strip()


def outline_signature(outline: Iterable[dict[str, Any]]) -> list[tuple[int, str, str]]:
    return [
        (int(item.get("level") or 0), str(item.get("number") or ""), _normalize_title(str(item.get("title") or "")))
        for item in outline
    ]


def assert_outline_unchanged(before: str, after: str, *, stage: str = "cleanup") -> None:
    before_outline = extract_markdown_outline(before, source_stage=f"{stage}:before")
    after_outline = extract_markdown_outline(after, source_stage=f"{stage}:after")
    if outline_signature(before_outline) != outline_signature(after_outline):
        raise ValueError(f"{stage} changed document outline, which is forbidden.")


def validate_outline_integrity(outline: list[dict[str, Any]]) -> dict[str, Any]:
    top = [item for item in outline if int(item.get("level") or 0) == 1 and str(item.get("number") or "").isdigit()]
    top_numbers = [int(item["number"]) for item in top]
    duplicate_top_numbers = sorted({num for num in top_numbers if top_numbers.count(num) > 1})
    repeated_sections: list[dict[str, str]] = []
    seen_sections: set[tuple[str, str]] = set()
    for item in outline:
        key = (str(item.get("number") or ""), _normalize_title(str(item.get("title") or "")))
        if key in seen_sections:
            repeated_sections.append({"number": key[0], "title": str(item.get("title") or "")})
        seen_sections.add(key)

    chapter_3_after_5 = False
    seen_5 = False
    previous_top = 0
    out_of_order: list[str] = []
    for num in top_numbers:
        if num == 5:
            seen_5 = True
        if seen_5 and num == 3:
            chapter_3_after_5 = True
        if previous_top and num < previous_top:
            out_of_order.append(f"{previous_top}->{num}")
        previous_top = num

    duplicate_chapter_2 = 2 in duplicate_top_numbers
    expected = list(range(min(top_numbers), max(top_numbers) + 1)) if top_numbers else []
    result = {
        "valid": not (duplicate_top_numbers or chapter_3_after_5 or out_of_order),
        "top_numbers": top_numbers,
        "expected_top_numbers": expected,
        "duplicate_top_numbers": duplicate_top_numbers,
        "duplicate_chapters_detected": [str(num) for num in duplicate_top_numbers],
        "duplicate_chapter_2": duplicate_chapter_2,
        "chapter_3_after_chapter_5": chapter_3_after_5,
        "top_level_out_of_order": out_of_order,
        "repeated_sections": repeated_sections,
    }
    if top_numbers and top_numbers != sorted(top_numbers):
        result["valid"] = False
    return result


def write_outline_debug(output_dir: Path, filename: str, outline: list[dict[str, Any]]) -> None:
    debug_dir = Path(output_dir) / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / filename).write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")


def _strip_front_matter(content: str) -> str:
    if not content.lstrip().startswith("---"):
        return content
    parts = content.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) >= 3 else content


def _normalize_title(text: str) -> str:
    cleaned = text.strip().lower()
    cleaned = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", cleaned)
    aliases = {
        "introduction": "引言",
        "mainbody": "文献综述",
        "body": "文献综述",
        "正文": "文献综述",
        "literaturereview": "文献综述",
        "methodology": "研究方法",
        "methods": "研究方法",
        "analysisandresults": "分析结果",
        "resultsandanalysis": "分析结果",
        "analysis": "分析结果",
        "results": "分析结果",
        "discussion": "讨论",
        "conclusion": "结论",
        "conclusions": "结论",
    }
    compact = re.sub(r"\s+", "", cleaned)
    if compact in aliases:
        return aliases[compact]
    return re.sub(r"\s+", "", cleaned)


def _is_caption_heading(title: str, number: str) -> bool:
    plain = title.strip()
    return bool(re.match(r"^(?:表|Table|图|Figure)\s*\d+", plain, flags=re.IGNORECASE)) or number.startswith("0.")


def _is_system_heading(title: str) -> bool:
    plain = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", title.strip()).lower()
    return plain in {"abstract", "摘要", "references", "bibliography", "参考文献", "table of contents", "目录"}
