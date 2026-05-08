#!/usr/bin/env python3
"""Shared document structure model for Markdown -> DOCX compilation."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Optional

from utils.text_utils import normalize_language_code as _normalize_language_code


SYSTEM_ROLES = {
    "abstract": {"abstract", "摘要"},
    "references": {"references", "bibliography", "参考文献"},
    "introduction": {"introduction", "引言"},
    "literature_review": {"literature review", "文献综述"},
    "methodology": {"methodology", "methods", "研究方法"},
    "results": {"analysis and results", "results and analysis", "analysis", "results", "分析结果"},
    "discussion": {"discussion", "讨论"},
    "conclusion": {"conclusion", "conclusions", "结论"},
}


DEFAULT_OUTLINE = {
    "zh": {
        "1": ("introduction", "引言"),
        "2": ("literature_review", "文献综述"),
        "3": ("methodology", "研究方法"),
        "4": ("results", "分析结果"),
        "5": ("discussion", "讨论"),
        "6": ("conclusion", "结论"),
    },
    "en": {
        "1": ("introduction", "Introduction"),
        "2": ("literature_review", "Literature Review"),
        "3": ("methodology", "Methodology"),
        "4": ("results", "Analysis and Results"),
        "5": ("discussion", "Discussion"),
        "6": ("conclusion", "Conclusion"),
    },
}


@dataclass
class AstNode:
    type: str
    text: str = ""
    children: list["AstNode"] = field(default_factory=list)


@dataclass
class Section(AstNode):
    role: str = "custom"
    level: int = 1
    number: str = ""
    title: str = ""
    language: str = "en"
    include_in_toc: bool = True
    toc_level: Optional[int] = 1
    word_style: str = "Heading 1"
    raw_level: int = 1
    source_line: int = 0

    def __init__(
        self,
        *,
        role: str = "custom",
        level: int = 1,
        number: str = "",
        title: str = "",
        language: str = "en",
        include_in_toc: bool = True,
        toc_level: Optional[int] = 1,
        word_style: str = "Heading 1",
        raw_level: int = 1,
        source_line: int = 0,
    ) -> None:
        super().__init__("Section", title, [])
        self.role = role
        self.level = level
        self.number = number
        self.title = title
        self.language = language
        self.include_in_toc = include_in_toc
        self.toc_level = toc_level
        self.word_style = word_style
        self.raw_level = raw_level
        self.source_line = source_line


@dataclass
class Document:
    language: str
    metadata: dict[str, Any] = field(default_factory=dict)
    blocks: list[AstNode] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    toc_depth: int = 3


def normalize_language_code(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"zh", "zh-cn", "zh_cn", "chinese", "cn", "中文"}:
        return "zh"
    if raw in {"en", "english", "英文"}:
        return "en"
    normalized = _normalize_language_code(value)
    return normalized if normalized in {"zh", "en"} else "en"


def parse_markdown_document(content: str, language: Any = None) -> Document:
    metadata = extract_front_matter(content)
    lang = normalize_language_code(language or metadata.get("language") or metadata.get("lang"))
    body = strip_front_matter(content)
    doc = Document(language=lang, metadata=metadata)
    stack: list[Section] = []

    for line_no, line in enumerate(body.splitlines(), start=1):
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            raw_level = len(heading.group(1))
            raw_title = clean_heading_text(heading.group(2), lang)
            number, title = split_heading_number(raw_title)
            number_depth = len(number.split(".")) if number else raw_level
            effective_level = min(max(raw_level, number_depth), 3)
            role = infer_role(title, number, lang)
            include = role not in {"toc"}
            toc_level: Optional[int] = effective_level if include else None
            word_style = f"Heading {effective_level}"
            if role == "abstract":
                word_style = "AbstractTitle"
                toc_level = 1
            elif role == "references":
                word_style = "ReferencesTitle"
                toc_level = 1
            section = Section(
                role=role,
                level=effective_level,
                number=number,
                title=title,
                language=lang,
                include_in_toc=include,
                toc_level=toc_level,
                word_style=word_style,
                raw_level=raw_level,
                source_line=line_no,
            )
            while stack and stack[-1].level >= effective_level:
                stack.pop()
            if stack:
                stack[-1].children.append(section)
            doc.sections.append(section)
            doc.blocks.append(section)
            stack.append(section)
            continue

        if is_table_start(body.splitlines(), line_no - 1):
            doc.tables.append({"index": len(doc.tables) + 1, "caption": "", "source_position": f"line:{line_no}"})

    return doc


def normalize_document_markdown(content: str, language: Any = None, *, renumber: bool = True) -> tuple[str, Document]:
    metadata = extract_front_matter(content)
    prefix = ""
    body = content
    if content.lstrip().startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            prefix = f"---{parts[1]}---\n\n"
            body = parts[2].lstrip("\n")
    lang = normalize_language_code(language or metadata.get("language") or metadata.get("lang"))
    lines = body.splitlines()
    doc = parse_markdown_document(content, lang)
    counters = [0, 0, 0]
    last_parent: dict[int, tuple[int, ...]] = {}
    current_top_role = ""
    current_top_num = ""
    top_h2_counts: dict[str, int] = {}
    pre_warnings: list[dict[str, str]] = []
    out: list[str] = []

    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            out.append(line)
            continue
        raw_level = len(match.group(1))
        raw_heading = clean_heading_text(match.group(2), lang)
        old_number, plain_title = split_heading_number(raw_heading)
        role = infer_role(plain_title, old_number, lang)
        number_depth = len(old_number.split(".")) if old_number else raw_level
        structural_level = max(raw_level, number_depth)

        if role == "toc":
            continue
        if role == "abstract":
            out.append(f"# {localized_role_title('abstract', lang)}")
            current_top_role = "abstract"
            current_top_num = ""
            continue
        if role == "references":
            out.append(f"# {localized_role_title('references', lang)}")
            current_top_role = "references"
            current_top_num = ""
            continue

        if raw_level == 1:
            current_top_role = role
            max_depth = max_heading_depth(role)
        else:
            max_depth = max_heading_depth(current_top_role)

        if structural_level > max_depth:
            out.append(f"**{plain_title}**")
            pre_warnings.append(
                {
                    "type": "heading_too_deep",
                    "message": f"Heading converted to bold paragraph: {plain_title}",
                    "action": "Converted over-depth heading after tree parsing.",
                }
            )
            continue

        level = min(structural_level, 3)
        if not plain_title.strip() and level > 1:
            pre_warnings.append(
                {
                    "type": "empty_heading",
                    "message": f"Empty heading removed: {raw_heading}",
                    "action": "Deleted empty leaf heading during AST normalization.",
                }
            )
            continue
        if not renumber:
            out.append(f"{'#' * level} {raw_heading}")
            continue

        if level == 1:
            top_num = old_number.split(".")[0] if old_number else ""
            if top_num.isdigit():
                counters = [int(top_num), 0, 0]
            else:
                counters = [counters[0] + 1, 0, 0]
            current_top_num = str(counters[0])
            number = current_top_num
            title = canonical_title_for_number(number, plain_title, lang)
            top_h2_counts.setdefault(number, 0)
            out.append(f"# {number}. {title}")
            continue

        if counters[0] == 0:
            counters[0] = int(old_number.split(".")[0]) if old_number and old_number.split(".")[0].isdigit() else 1
        for parent_idx in range(1, level - 1):
            if counters[parent_idx] == 0:
                counters[parent_idx] = 1
        current_top_num = str(counters[0])
        parent = tuple(counters[: level - 1])
        if last_parent.get(level) != parent:
            counters[level - 1] = 0
            last_parent[level] = parent
        counters[level - 1] += 1
        for idx in range(level, 3):
            counters[idx] = 0
        number = ".".join(str(part) for part in counters[:level])
        if level == 2:
            top_h2_counts[current_top_num] = top_h2_counts.get(current_top_num, 0) + 1
        out.append(f"{'#' * level} {number} {plain_title}")

    normalized_body = _remove_empty_leaf_headings("\n".join(out).strip(), pre_warnings)
    normalized = prefix + normalized_body + "\n"
    doc = parse_markdown_document(normalized, lang)
    doc.warnings.extend(pre_warnings)
    for top, count in top_h2_counts.items():
        if count > 8:
            doc.warnings.append(
                {
                    "type": "heading_flattening_suspected_high" if count > 12 else "heading_flattening_suspected",
                    "message": f"Chapter {top} has {count} Heading 2 sections.",
                    "action": "Review outline for accidental flattening.",
                }
            )
    heading_2_total = sum(1 for section in doc.sections if section.word_style == "Heading 2")
    heading_3_total = sum(1 for section in doc.sections if section.word_style == "Heading 3")
    if heading_3_total == 0 and heading_2_total > 8:
        doc.warnings.append(
            {
                "type": "heading_flattening_suspected_high",
                "message": "Heading 3 count is zero while Heading 2 count is high.",
                "action": "Review outline for accidental flattening.",
            }
        )
    _mark_empty_heading_warnings(normalized, doc)
    return normalized, doc


def build_document_structure_manifest(content: str, language: Any = None) -> dict[str, Any]:
    normalized, doc = normalize_document_markdown(content, language)
    metadata = doc.metadata
    front: list[dict[str, Any]] = []
    body: list[dict[str, Any]] = []
    back: list[dict[str, Any]] = []
    for section in doc.sections:
        item = section_to_manifest(section)
        if section.role == "abstract":
            front.append(item)
        elif section.role == "references":
            back.append(item)
        elif section.role != "toc":
            body.append(item)

    tables = []
    for idx, line in enumerate(normalized.splitlines(), start=1):
        caption = re.match(r"^\s*(?:\*\*)?(表\s*(\d+)|Table\s+(\d+))\s*[.:：]?\s*(.+?)(?:\*\*)?\s*$", line, re.IGNORECASE)
        if caption:
            tables.append(
                {
                    "index": int(caption.group(2) or caption.group(3)),
                    "caption": caption.group(4).strip(),
                    "source_position": f"line:{idx}",
                }
            )

    return {
        "language": doc.language,
        "title": str(metadata.get("title") or "").strip(),
        "toc_depth": 3,
        "front_matter": front,
        "body_sections": body,
        "back_matter": back,
        "tables": tables,
        "warnings": doc.warnings,
    }


def section_to_manifest(section: Section) -> dict[str, Any]:
    return {
        "role": section.role,
        "level": section.level,
        "number": section.number,
        "title": section.title,
        "language": section.language,
        "word_style": section.word_style,
        "include_in_toc": section.include_in_toc,
        "toc_level": section.toc_level,
    }


def extract_front_matter(content: str) -> dict[str, Any]:
    if not content.lstrip().startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    data: dict[str, Any] = {}
    for line in parts[1].splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip("'\"")
    return data


def strip_front_matter(content: str) -> str:
    if not content.lstrip().startswith("---"):
        return content
    parts = content.split("---", 2)
    return parts[2].lstrip("\n") if len(parts) >= 3 else content


def split_heading_number(heading: str) -> tuple[str, str]:
    heading = re.sub(r"^[·•\-*]\s+", "", heading.strip())
    number_only = re.match(r"^(\d+(?:\.\d+)*)\.?\s*$", heading)
    if number_only:
        return number_only.group(1), ""
    match = re.match(r"^(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$", heading)
    if not match:
        return "", heading
    return match.group(1), match.group(2).strip()


def clean_heading_text(heading: str, language: str) -> str:
    text = re.sub(r"^[·•\-*]\s+", "", heading.strip())
    if language == "zh":
        text = re.sub(r"^(\d+(?:\.\d+)*\.?\s+)[一二三四五六七八九十]+[、.．]\s*", r"\1", text)
    return text


def infer_role(title: str, number: str = "", language: str = "en") -> str:
    plain = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", title.strip()).strip()
    key = plain.lower()
    if key in {"table of contents", "目录"}:
        return "toc"
    for role, names in SYSTEM_ROLES.items():
        if key in names or plain in names:
            return role
    top = number.split(".")[0] if number else ""
    if top in DEFAULT_OUTLINE[normalize_language_code(language)]:
        return DEFAULT_OUTLINE[normalize_language_code(language)][top][0]
    return "custom"


def localized_role_title(role: str, language: str) -> str:
    if role == "abstract":
        return "摘要" if language == "zh" else "Abstract"
    if role == "references":
        return "参考文献" if language == "zh" else "References"
    return role


def canonical_title_for_number(number: str, fallback: str, language: str) -> str:
    top = number.split(".")[0]
    outline = DEFAULT_OUTLINE[normalize_language_code(language)]
    fallback_key = fallback.strip().lower()
    role = outline.get(top, ("custom", ""))[0]
    role_names = SYSTEM_ROLES.get(role, set())
    generic_names = {"main body", "body", "正文"}
    if top in outline and (not fallback.strip() or fallback_key in role_names or fallback in role_names or fallback_key in generic_names):
        return outline[top][1]
    return fallback


def max_heading_depth(role: str) -> int:
    if role in {"abstract", "references"}:
        return 1
    if role in {"introduction"}:
        return 3
    if role in {"conclusion"}:
        return 3
    return 3


def is_table_start(lines: list[str], idx: int) -> bool:
    if idx + 1 >= len(lines):
        return False
    return "|" in lines[idx] and re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", lines[idx + 1]) is not None


def _mark_empty_heading_warnings(content: str, doc: Document) -> None:
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if not re.match(r"^#{1,6}\s+", line):
            continue
        cursor = idx + 1
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        if cursor < len(lines) and re.match(r"^#{1,6}\s+", lines[cursor]):
            current_level = len(re.match(r"^(#{1,6})\s+", line).group(1))
            next_level = len(re.match(r"^(#{1,6})\s+", lines[cursor]).group(1))
            if next_level > current_level:
                continue
            title = re.sub(r"^#{1,6}\s+", "", line).strip()
            doc.warnings.append(
                {
                    "type": "empty_heading",
                    "message": f"Heading has no body before next same-or-higher heading: {title}",
                    "action": "Recorded empty leaf heading for final validation.",
                }
            )


def _remove_empty_leaf_headings(content: str, warnings: list[dict[str, str]]) -> str:
    lines = content.splitlines()
    remove: set[int] = set()
    for idx, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        current_level = len(match.group(1))
        title = match.group(2).strip()
        _number, plain = split_heading_number(title)
        cursor = idx + 1
        while cursor < len(lines) and not lines[cursor].strip():
            cursor += 1
        if cursor >= len(lines):
            remove.add(idx)
        else:
            next_heading = re.match(r"^(#{1,6})\s+", lines[cursor])
            if next_heading and len(next_heading.group(1)) <= current_level:
                remove.add(idx)
        if not plain.strip():
            remove.add(idx)
        if idx in remove:
            warnings.append(
                {
                    "type": "empty_heading",
                    "message": f"Empty heading removed or downgraded: {title}",
                    "action": "Deleted empty leaf heading during AST normalization.",
                }
            )
    return "\n".join(line for idx, line in enumerate(lines) if idx not in remove).strip()
