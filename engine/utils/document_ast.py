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


RESEARCH_BODY_WRAPPERS = {
    "zh": {
        "2.1": ("2", "文献综述"),
        "2.2": ("3", "研究方法"),
        "2.3": ("4", "分析结果"),
        "2.4": ("5", "讨论"),
        "1.1": ("2", "文献综述"),
        "1.2": ("3", "研究方法"),
        "1.3": ("4", "分析结果"),
        "1.4": ("5", "讨论"),
    },
    "en": {
        "2.1": ("2", "Literature Review"),
        "2.2": ("3", "Methodology"),
        "2.3": ("4", "Analysis and Results"),
        "2.4": ("5", "Discussion"),
        "1.1": ("2", "Literature Review"),
        "1.2": ("3", "Methodology"),
        "1.3": ("4", "Analysis and Results"),
        "1.4": ("5", "Discussion"),
    },
}


BODY_HEADING_ROLES = {
    "literature_review",
    "methodology",
    "results",
    "discussion",
    "conclusion",
    "custom",
}


ABSTRACT_BOLD_LABELS = {
    "研究问题与方法",
    "研究问题与方法：",
    "方法与发现",
    "方法与发现：",
    "研究方法与主要发现",
    "研究方法与主要发现：",
    "主要贡献",
    "主要贡献：",
    "理论与实践意义",
    "理论与实践意义：",
    "关键词",
    "关键词：",
    "abstract",
    "abstract:",
    "keywords",
    "keywords:",
    "research problem and approach",
    "research problem and approach:",
    "methodology and findings",
    "methodology and findings:",
    "key contributions",
    "key contributions:",
    "implications",
    "implications:",
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


@dataclass
class BodySection:
    source_file: str = ""
    original_heading_level: int = 1
    original_number: str = ""
    original_title: str = ""
    normalized_level: int = 1
    normalized_number: str = ""
    normalized_title: str = ""
    children: list["BodySection"] = field(default_factory=list)
    body_blocks: list[str] = field(default_factory=list)
    source_line: int = 0


@dataclass
class BodyAST:
    source_file: str = ""
    language: str = "en"
    sections: list[BodySection] = field(default_factory=list)
    preamble_blocks: list[str] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)


def parse_body_ast(body_md: str, source_file: str = "02_main_body.md", language: Any = None) -> BodyAST:
    """Parse the selected body markdown into a source-preserving section tree."""
    lang = normalize_language_code(language)
    ast = BodyAST(source_file=source_file, language=lang)
    stack: list[BodySection] = []

    for line_no, line in enumerate((body_md or "").splitlines(), start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            if stack:
                stack[-1].body_blocks.append(line)
            else:
                ast.preamble_blocks.append(line)
            continue

        raw_level = len(match.group(1))
        heading = clean_heading_text(match.group(2), lang)
        number, title = split_heading_number(heading)
        section = BodySection(
            source_file=source_file,
            original_heading_level=raw_level,
            original_number=number,
            original_title=title,
            normalized_level=raw_level,
            normalized_number=number,
            normalized_title=title,
            source_line=line_no,
        )
        while stack and stack[-1].original_heading_level >= raw_level:
            stack.pop()
        if stack:
            stack[-1].children.append(section)
        else:
            ast.sections.append(section)
        stack.append(section)

    return ast


def normalize_body_ast(body_ast: BodyAST, outline_schema: Optional[dict[str, Any]] = None) -> BodyAST:
    """Normalize body section numbers once, preserving the parsed parent/child tree."""
    return normalize_final_outline(body_ast, outline_schema)


def normalize_final_outline(body_ast: BodyAST, outline_schema: Optional[dict[str, Any]] = None) -> BodyAST:
    """The single body-outline numbering function used by compile."""
    lang = normalize_language_code(body_ast.language)
    schema = outline_schema or DEFAULT_OUTLINE.get(lang, DEFAULT_OUTLINE["en"])
    body_ast.language = lang
    counters: dict[str, list[int]] = {}

    def canonical_top(number: str, fallback: str) -> str:
        item = schema.get(number)
        if isinstance(item, tuple) and len(item) >= 2:
            return str(item[1])
        if isinstance(item, dict):
            return str(item.get("title") or fallback)
        return fallback

    def wrapper_target(section: BodySection) -> tuple[str, str] | None:
        number = section.original_number.rstrip(".")
        wrappers = RESEARCH_BODY_WRAPPERS.get(lang, RESEARCH_BODY_WRAPPERS["en"])
        if number in wrappers:
            top, title = wrappers[number]
            return top, title
        parts = number.split(".") if number else []
        if len(parts) == 1 and parts[0] in {"2", "3", "4", "5"}:
            return parts[0], canonical_top(parts[0], section.original_title)
        return None

    def normalize_descendant(section: BodySection, top_number: str, source_prefix: str, depth_from_chapter: int) -> None:
        source_number = section.original_number.rstrip(".")
        remainder: list[str] = []
        if source_prefix and source_number.startswith(source_prefix + "."):
            remainder = source_number[len(source_prefix) + 1 :].split(".")
        elif source_number:
            parts = source_number.split(".")
            remainder = parts[1:] if len(parts) > 1 else []

        normalized_level = min(max(depth_from_chapter + 1, 2), 3)
        if remainder:
            suffix = ".".join(remainder[: normalized_level - 1])
            normalized_number = ".".join([top_number, suffix])
        else:
            key = f"{top_number}:{normalized_level}"
            counter = counters.setdefault(key, [0])
            counter[0] += 1
            normalized_number = f"{top_number}.{counter[0]}"

        section.normalized_level = normalized_level
        section.normalized_number = normalized_number
        section.normalized_title = section.original_title

        child_prefix = source_number or source_prefix
        for child in section.children:
            normalize_descendant(child, top_number, child_prefix, depth_from_chapter + 1)

    for root in body_ast.sections:
        target = wrapper_target(root)
        if target:
            top_number, top_title = target
            source_prefix = root.original_number.rstrip(".")
        else:
            top_number = root.original_number.split(".")[0] if root.original_number else "2"
            if top_number not in {"2", "3", "4", "5"}:
                top_number = "2"
            top_title = canonical_top(top_number, root.original_title)
            source_prefix = root.original_number.rstrip(".")
            body_ast.warnings.append(
                {
                    "type": "body_outline_inferred_top",
                    "message": f"Inferred body chapter {top_number} from heading {root.original_number} {root.original_title}".strip(),
                    "action": "Normalized once in normalize_final_outline.",
                }
            )

        root.normalized_level = 1
        root.normalized_number = top_number
        root.normalized_title = top_title
        counters[f"{top_number}:2"] = [0]
        counters[f"{top_number}:3"] = [0]
        for child in root.children:
            normalize_descendant(child, top_number, source_prefix, 1)

    return body_ast


def render_body_ast_to_markdown(body_ast: BodyAST) -> str:
    """Render the frozen body AST back to Markdown without additional remapping."""
    out: list[str] = []

    def append_blocks(blocks: list[str], parent_number: str = "", parent_level: int = 0) -> None:
        if not blocks:
            return
        if out and out[-1].strip():
            out.append("")
        promoted = _render_body_blocks_with_promoted_subheads(blocks, parent_number, parent_level)
        out.extend(promoted)

    def render_section(section: BodySection) -> None:
        if out and out[-1].strip():
            out.append("")
        level = min(max(section.normalized_level, 1), 3)
        number = section.normalized_number.strip()
        title = section.normalized_title.strip()
        suffix = "." if level == 1 and number else ""
        heading_text = f"{number}{suffix} {title}".strip()
        out.append(f"{'#' * level} {heading_text}")
        append_blocks(section.body_blocks, number, level)
        for child in section.children:
            render_section(child)

    append_blocks(body_ast.preamble_blocks)
    for root in body_ast.sections:
        render_section(root)

    return "\n".join(out).strip()


def _render_body_blocks_with_promoted_subheads(blocks: list[str], parent_number: str, parent_level: int) -> list[str]:
    if parent_level != 2 or not parent_number:
        return blocks
    out: list[str] = []
    counter = 0
    for line in blocks:
        match = re.match(r"^\s*\*\*([^*\n]+?)\*\*\s*$", line.strip())
        if not match:
            out.append(line)
            continue
        title = match.group(1).strip()
        if _is_forbidden_bold_subheading(title, "zh") or _is_forbidden_bold_subheading(title, "en"):
            out.append(line)
            continue
        counter += 1
        out.append(f"### {parent_number}.{counter} {title}")
    return out


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
    body = promote_bold_subheadings(body, lang)
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


def normalize_research_body_markdown(content: str, language: Any = None) -> tuple[str, Document]:
    """Normalize generated 2.x research-paper body sections through the AST contract."""
    lang = normalize_language_code(language)
    mapped = promote_research_body_wrappers(content, lang)
    mapped = promote_bold_subheadings(mapped, lang)
    return normalize_document_markdown(mapped, lang)


def promote_research_body_wrappers(content: str, language: Any = None) -> str:
    """Map generated body wrapper headings to final research-paper chapters."""
    lang = normalize_language_code(language)
    wrappers = RESEARCH_BODY_WRAPPERS.get(lang, RESEARCH_BODY_WRAPPERS["en"])
    lines: list[str] = []

    for line in (content or "").splitlines():
        match = re.match(r"^(#{2,6})\s+((?:1|2)\.([1-4])(?:\.(\d+(?:\.\d+)*))?)\.?\s+(.+?)\s*$", line)
        if not match:
            if re.match(r"^#\s+(?:2\.?\s*)?(?:正文|Main Body|Body)\s*$", line, flags=re.IGNORECASE):
                continue
            lines.append(line)
            continue

        _hashes, source_number, section_idx, nested_rest, title = match.groups()
        wrapper_key = ".".join(source_number.split(".")[:2])
        target = wrappers.get(wrapper_key)
        if not target:
            lines.append(line)
            continue

        final_top, final_title = target
        if not nested_rest:
            lines.append(f"# {final_top}. {final_title}")
            continue

        final_parts = nested_rest.split(".")
        final_level = min(len(final_parts) + 1, 3)
        final_number = ".".join([final_top] + final_parts[: final_level - 1])
        lines.append(f"{'#' * final_level} {final_number} {title.strip()}")

    return "\n".join(lines).strip()


def promote_bold_subheadings(content: str, language: Any = None) -> str:
    """Promote standalone bold body labels into Heading 3 when they are true subheads."""
    lang = normalize_language_code(language)
    lines = (content or "").splitlines()
    out: list[str] = []
    current_role = ""
    current_top = ""
    in_code = False
    in_references = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            out.append(line)
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading and not in_code:
            raw_heading = clean_heading_text(heading.group(2), lang)
            number, title = split_heading_number(raw_heading)
            role = infer_role(title, number, lang)
            if role != "custom" or number:
                current_role = role
            current_top = number.split(".")[0] if number else current_top
            in_references = role == "references"
            out.append(line)
            continue

        if (
            not in_code
            and not in_references
            and _is_promotable_bold_subheading_line(line, lines, idx, lang, current_role, current_top)
        ):
            title = _extract_bold_line_text(line)
            out.append(f"### {title}")
            continue

        out.append(line)

    return "\n".join(out)


def _extract_bold_line_text(line: str) -> str:
    match = re.match(r"^\s*\*\*(.+?)\*\*\s*$", line.strip())
    return match.group(1).strip() if match else line.strip().strip("*").strip()


def _is_promotable_bold_subheading_line(
    line: str,
    lines: list[str],
    idx: int,
    language: str,
    current_role: str,
    current_top: str,
) -> bool:
    title = _extract_bold_line_text(line)
    if not re.match(r"^\s*\*\*[^*\n]+?\*\*\s*$", line.strip()):
        return False
    if not _is_body_heading_context(current_role, current_top):
        return False
    if _is_forbidden_bold_subheading(title, language):
        return False
    if re.search(r"[。；;，,、.!?？：:]\s*$", title):
        return False
    if not _bold_subheading_length_ok(title, language):
        return False
    return _next_nonempty_line_is_body(lines, idx + 1)


def _is_body_heading_context(current_role: str, current_top: str) -> bool:
    if current_role in {"abstract", "references", "toc", "introduction"}:
        return False
    if current_role in BODY_HEADING_ROLES and current_top != "1":
        return True
    return current_top in {"2", "3", "4", "5", "6"}


def _is_forbidden_bold_subheading(title: str, language: str) -> bool:
    normalized = re.sub(r"\s+", " ", title.strip()).strip()
    key = normalized.lower().rstrip()
    if key in ABSTRACT_BOLD_LABELS:
        return True
    if re.match(r"^(?:表\s*\d+|Table\s+\d+|图\s*\d+|Figure\s+\d+|注|Note)\s*[:：.]?", normalized, flags=re.IGNORECASE):
        return True
    if language == "zh" and normalized in {"摘要", "关键词", "目录", "参考文献"}:
        return True
    return False


def _bold_subheading_length_ok(title: str, language: str) -> bool:
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", title))
    if cjk_count:
        return 5 <= cjk_count <= 40
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", title)
    return 3 <= len(words) <= 12


def _next_nonempty_line_is_body(lines: list[str], start: int) -> bool:
    cursor = start
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if not stripped:
            cursor += 1
            continue
        if stripped.startswith("```"):
            return False
        if re.match(r"^#{1,6}\s+", stripped):
            return False
        if re.match(r"^\s*\|", stripped) or re.match(r"^\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", stripped):
            return False
        if re.match(r"^\s*(?:<!--\s*PAGEBREAK\s*-->|\\newpage|/newpage|newpage)\s*$", stripped, flags=re.IGNORECASE):
            return False
        if re.match(r"^\s*\*\*[^*\n]+?\*\*\s*$", stripped):
            return False
        return True
    return False


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
