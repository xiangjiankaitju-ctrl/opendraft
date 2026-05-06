#!/usr/bin/env python3
"""Regression tests for Chinese-localized templates and abstract placeholder handling."""

import os
import re
import sys
from pathlib import Path

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))

from phases.context import DraftContext
from utils.abstract_generator import replace_placeholder_with_abstract, _measure_abstract_length
from phases.compile import (
    _apply_structure_aware_final_cleanup,
    _clean_chinese_final_artifacts,
    _normalize_doi_url_case,
    _normalize_yaml_language,
    _localize_chinese_abstract_labels,
    _normalize_chinese_body_outline,
    _normalize_chinese_final_page_breaks,
    _normalize_markdown_page_breaks,
    _remove_compile_artifact_paragraphs,
    _assert_markdown_table_rows_not_reduced,
    _prepare_body_section,
    _select_compile_section_texts,
    _assemble_markdown_body,
    normalize_main_body_headings_for_zh,
    normalize_conclusion_headings_for_final,
    finalize_or_repair_markdown,
    repair_heading_numbering,
    repair_pagebreaks,
    repair_table_captions,
    collapse_duplicate_pagebreaks,
    validate_final_markdown,
    _validate_final_markdown,
)
from phases.compose import _enforce_body_section_numbering, validate_main_body_outline
from utils.text_cleanup import apply_full_cleanup
from utils.text_utils import clean_ai_language, localize_chapter_headings, normalize_language_code


class TestChineseLocalization:
    def test_localize_main_body_heading_to_chinese(self):
        text = "# 2. Main Body\ncontent\n\n# 5. References"
        localized = localize_chapter_headings(text, "zh")

        assert "# 2. 正文" in localized
        assert "# 5. 参考文献" in localized

    def test_replace_chinese_abstract_placeholder(self):
        draft = "## 摘要\n[摘要将在编译阶段自动生成]\n\\newpage\n\n# 1. 引言"
        updated = replace_placeholder_with_abstract(
            draft,
            "**Research Problem and Approach:** 这是一个符合要求的中文摘要。",
            "chinese",
        )

        assert "这是一个符合要求的中文摘要。" in updated
        assert "Research Problem and Approach" not in updated
        assert "研究问题与方法" in updated
        assert "\\newpage" not in updated
        assert "<!-- PAGEBREAK -->" in updated
        assert "[摘要将在编译阶段自动生成]" not in updated

    def test_chinese_abstract_length_uses_cjk_chars(self):
        text = "人工智能推动新质生产力发展" * 20
        measured = _measure_abstract_length(text, "chinese")
        assert measured >= 200

    def test_language_aliases_normalize_to_export_buckets(self):
        assert normalize_language_code("zh-CN") == "zh"
        assert normalize_language_code("chinese") == "zh"
        assert normalize_language_code("cn") == "zh"
        assert normalize_language_code("中文") == "zh"
        assert normalize_language_code("english") == "en"
        assert normalize_language_code("英文") == "en"

    def test_normalize_conclusion_headings_repairs_chinese_3x(self):
        normalized = normalize_conclusion_headings_for_final(
            "# 3. 结论\n## 3.1 研究总结与管理启示\n正文。\n## 3.2 研究局限与未来展望\n正文。",
            "zh",
        )

        assert "# 6. 结论" in normalized
        assert "## 6.1 研究总结与管理启示" in normalized
        assert "## 6.2 研究局限与未来展望" in normalized
        assert "## 3.1" not in normalized

    def test_normalize_conclusion_headings_adds_missing_numbers(self):
        normalized = normalize_conclusion_headings_for_final(
            "# 结论\n## 研究总结与管理启示\n正文。\n## 研究局限与未来展望\n正文。",
            "zh",
        )

        assert normalized.startswith("# 6. 结论")
        assert "## 6.1 研究总结与管理启示" in normalized
        assert "## 6.2 研究局限与未来展望" in normalized

    def test_finalize_repairs_duplicate_method_and_conclusion_heading_numbers(self):
        final, report = finalize_or_repair_markdown(
            "# 3. 研究方法\n## 3.1 研究设计与逻辑\n正文。\n# 6. 结论\n## 3.1 研究总结与管理启示\n正文。",
            "zh",
        )

        assert "## 3.1 研究设计与逻辑" in final
        assert "## 6.1 研究总结与管理启示" in final
        assert "## 3.1 研究总结" not in final
        assert "conclusion_numbering" in report["auto_fixed"]
        assert validate_final_markdown(final, "zh")["errors"] == []

    def test_repair_pagebreaks_collapses_consecutive_markers(self):
        repaired = repair_pagebreaks("A\n\n<!-- PAGEBREAK --><!-- PAGEBREAK -->\n\nB")

        assert repaired.count("<!-- PAGEBREAK -->") == 1

    def test_collapse_duplicate_pagebreaks_removes_mixed_stack(self):
        repaired = collapse_duplicate_pagebreaks("摘要\n\\newpage\n<!-- PAGEBREAK -->\n\n# 1. 引言")

        assert repaired.count("<!-- PAGEBREAK -->") == 1
        assert "\\newpage" not in repaired

    def test_repair_table_captions_renumbers_globally(self):
        repaired = repair_table_captions("表 1：文献表\n\n| A | B |\n|---|---|\n\n表 1：方法表", "zh")

        assert "表1：文献表" in repaired
        assert "表2：方法表" in repaired

    def test_repair_table_captions_handles_section_numbers_without_body_duplication(self):
        repaired = repair_table_captions(
            "表 6.1：主要研究发现与管理启示摘要\n\n| A | B |\n|---|---|\n\n正文引用表 6.1 总结了管理启示。",
            "zh",
        )

        assert "表1：主要研究发现与管理启示摘要" in repaired
        assert "表1：1：" not in repaired
        assert "表1 总结了管理启示" in repaired

    def test_strict_wrapper_still_raises_for_duplicate_numbering(self):
        with pytest.raises(ValueError, match="Duplicate numbered heading"):
            _validate_final_markdown("# 3. 研究方法\n## 3.1 A\n## 3.1 B", "zh")

    def test_clean_chinese_final_artifacts_removes_toc_placeholder_and_english_templates(self):
        dirty = """# 目录
Table of Contents

## 摘要
[Abstract will be generated]

2.4.1 理论框架的验证与拓展
The findings FROM LITERATURE presented in section 2.3 ...
Compared to the theoretical framework in section 2.1 ...
中文分析段落保留。
"""
        cleaned = _clean_chinese_final_artifacts(dirty)
        assert "Table of Contents" not in cleaned
        assert "[Abstract will be generated]" not in cleaned
        assert "The findings FROM LITERATURE" not in cleaned
        assert "Compared to the theoretical framework" not in cleaned
        assert "中文分析段落保留" in cleaned

    def test_clean_chinese_final_artifacts_preserves_technical_table_rows(self):
        dirty = """# 2. 正文

| 技术路径 | 核心算法 | 性能指标 |
| --- | --- | --- |
| 图像增强 + 检测 | Extended ESRGAN + SSD | 准确率 79%, PSNR 提升 |
| 单阶段检测器 | YOLOv5 | 高实时性 |

This line is mostly English template filler with enough words to be removed from Chinese body text.
"""
        cleaned = _clean_chinese_final_artifacts(dirty)

        assert "| 图像增强 + 检测 | Extended ESRGAN + SSD | 准确率 79%, PSNR 提升 |" in cleaned
        assert "| 单阶段检测器 | YOLOv5 | 高实时性 |" in cleaned

    def test_chinese_final_markdown_normalizes_abstract_pagebreak_and_body_outline(self):
        text = """## 摘要
**Research Problem and Approach:** 中文摘要。
ewpage

# 2. 正文
### 1.1. 农业智能感知的技术演进
#### 1.1.1. 数据处理挑战

# 3. 结论
正文。
"""
        text = _localize_chinese_abstract_labels(text)
        text = _normalize_chinese_final_page_breaks(text)
        text = _normalize_chinese_body_outline(text)

        assert "Research Problem and Approach" not in text
        assert "**研究问题与方法:** 中文摘要。" in text
        assert "ewpage" not in [line.strip() for line in text.splitlines()]
        assert "<!-- PAGEBREAK -->" in text
        assert "## 2.1. 农业智能感知的技术演进" in text
        assert "### 2.1.1. 数据处理挑战" in text
        assert "### 1.1." not in text

    def test_final_cleanup_protects_tables_doi_and_yaml_language(self):
        dirty = """---
title: 测试
language: zh-cn
---

## Abstract
**Research Problem and Approach:** 中文摘要。

ewpage

# 1. 引言

| 技术路径 | 核心算法 | DOI |
| --- | --- | --- |
| 图像增强 + 检测 | Extended ESRGAN + SSD | Https://doi.org/10.1000/ABC |
| 单阶段检测器 | YOLOv5 | https://doi.org/10.1000/XYZ |

这是一个普通段落，in conclusion, 需要被普通段落清理。
"""
        cleaned = _apply_structure_aware_final_cleanup(dirty, "zh", apply_full_cleanup, clean_ai_language)["text"]
        cleaned = _localize_chinese_abstract_labels(cleaned)
        cleaned = _normalize_yaml_language(cleaned, "zh")
        cleaned = _normalize_doi_url_case(cleaned)
        cleaned = _normalize_chinese_final_page_breaks(cleaned)

        assert 'language: "zh"' in cleaned
        assert "Research Problem and Approach" not in cleaned
        assert "ewpage" not in [line.strip() for line in cleaned.splitlines()]
        assert "<!-- PAGEBREAK -->" in cleaned
        assert "Https://doi.org" not in cleaned
        assert "| 图像增强 + 检测 | Extended ESRGAN + SSD | https://doi.org/10.1000/ABC |" in cleaned
        assert "| 单阶段检测器 | YOLOv5 | https://doi.org/10.1000/XYZ |" in cleaned
        _assert_markdown_table_rows_not_reduced(dirty, cleaned)

    def test_prepare_body_section_keeps_first_meaningful_body_heading(self):
        body = """## 2.1 文献综述

### 2.1.1 农业智能感知
正文。
"""
        prepared = _prepare_body_section(body, "zh")

        assert prepared.startswith("## 2.1 文献综述")
        assert "### 2.1.1 农业智能感知" in prepared

    def test_normalize_main_body_headings_for_zh_maps_once_and_preserves_semantics(self):
        body = """## 2.1 文献综述
### 2.1.1 理论基础与发展脉络
### 2.1.2 技术路线比较分析

| 指标 | 结果 |
| --- | --- |
| A | B |

## 2.2 方法论
### 2.2.1 研究设计与分析框架
### 2.2.2 技术路径选择依据

## 2.3 分析与结果
### 2.3.1 技术模块效能对比分析

## 2.4 讨论
### 2.4.1 技术路径选择与应用场景的协同演化
"""
        normalized = normalize_main_body_headings_for_zh(body)

        assert "# 2. 文献综述" in normalized
        assert "## 2.1 理论基础与发展脉络" in normalized
        assert "## 2.2 技术路线比较分析" in normalized
        assert "# 3. 研究方法" in normalized
        assert "## 3.1 研究设计与分析框架" in normalized
        assert "## 3.2 技术路径选择依据" in normalized
        assert "# 4. 分析结果" in normalized
        assert "## 4.1 技术模块效能对比分析" in normalized
        assert "# 5. 讨论" in normalized
        assert "## 5.1 技术路径选择与应用场景的协同演化" in normalized
        assert "# 3. 研究方法\n### 2.1.2" not in normalized
        assert "| A | B |" in normalized

    def test_stable_compile_uses_02_main_body_only_and_final_headings_are_continuous(self, tmp_path):
        drafts = tmp_path / "drafts"
        exports = tmp_path / "exports"
        drafts.mkdir()
        exports.mkdir()
        (drafts / "01_introduction.md").write_text("# 1. 引言\n引言正文。", encoding="utf-8")
        (drafts / "02_main_body.md").write_text("""## 2.1 文献综述
### 2.1.1 理论基础与发展脉络
正文。

## 2.2 方法论
### 2.2.1 研究设计与分析框架
正文。

## 2.3 分析与结果
### 2.3.1 技术模块效能对比分析
正文。

| 技术 | 效能 |
| --- | --- |
| A | 高 |

## 2.4 讨论
### 2.4.1 技术路径选择与应用场景的协同演化
正文。
""", encoding="utf-8")
        (drafts / "02_1_literature_review.md").write_text("# 2. 文献综述\n不应读取。", encoding="utf-8")
        (drafts / "02_2_methodology.md").write_text("# 3. 研究方法\n不应读取。", encoding="utf-8")
        (drafts / "02_3_analysis_results.md").write_text("# 4. 分析结果\n不应读取。", encoding="utf-8")
        (drafts / "02_4_discussion.md").write_text("# 5. 讨论\n不应读取。", encoding="utf-8")
        (drafts / "03_conclusion.md").write_text("# 6. 结论\n结论正文。", encoding="utf-8")

        ctx = DraftContext(language="zh", academic_level="research_paper", folders={"drafts": drafts, "exports": exports})
        intro, body, conclusion = _select_compile_section_texts(ctx, lambda text: text)
        final = _assemble_markdown_body(ctx, intro, body, conclusion, "")
        final = _remove_compile_artifact_paragraphs(final)
        final = _normalize_markdown_page_breaks(final, output="comment")

        top_numbers = [int(m.group(1)) for m in re.finditer(r"(?m)^#\s+(\d+)\.\s+", final)]
        assert top_numbers == [1, 2, 3, 4, 5, 6]
        assert final.count("# 2. 文献综述") == 1
        assert final.count("# 3. 研究方法") == 1
        assert final.count("# 4. 分析结果") == 1
        assert "不应读取" not in final
        assert "## 2.1 理论基础与发展脉络" in final
        assert "## 3.1 研究设计与分析框架" in final
        assert "| A | 高 |" in final
        _validate_final_markdown(final, "zh")

    def test_split_body_sections_are_forced_into_distinct_2x_namespaces(self):
        method = _enforce_body_section_numbering(
            """## 2.1 研究方法
### 2.1.1 研究设计与理论分析框架
正文。
""",
            "methodology",
            "zh",
        )
        results = _enforce_body_section_numbering(
            """## 2.1 分析与结果
### 2.1.1 技术模块效能对比分析
正文。
""",
            "results",
            "zh",
        )

        assert method.startswith("## 2.2 研究方法")
        assert "### 2.2.1 研究设计与理论分析框架" in method
        assert results.startswith("## 2.3 分析与结果")
        assert "### 2.3.1 技术模块效能对比分析" in results

    def test_main_body_outline_validation_blocks_repeated_21_before_compile(self):
        polluted = """## 2.1 文献综述
### 2.1.1 理论基础

## 2.1 研究方法
### 2.2.1 研究设计

## 2.1 分析与结果
### 2.3.1 分析
"""
        try:
            validate_main_body_outline(polluted)
        except ValueError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected polluted 02_main_body.md to fail validation")

        assert "multiple ## 2.1" in message
        assert "must contain ## 2.1, ## 2.2, ## 2.3, ## 2.4 in order" in message

    def test_compile_artifact_and_pagebreak_cleanup(self):
        dirty = """正文。

Concept alignment note:
This paper explicitly operationalizes the topic.
evidence-to-claim mapping

\\newpage<!-- PAGEBREAK -->

后文。
"""
        cleaned = _remove_compile_artifact_paragraphs(dirty)
        cleaned = _normalize_markdown_page_breaks(cleaned, output="comment")

        assert "Concept alignment note:" not in cleaned
        assert "This paper explicitly operationalizes" not in cleaned
        assert "evidence-to-claim mapping" not in cleaned
        assert "\\newpage<!-- PAGEBREAK -->" not in cleaned
        assert "<!-- PAGEBREAK -->" in cleaned


class TestChineseLanguageInstruction:
    def test_draft_context_supports_citation_metrics(self):
        ctx = DraftContext(language="zh-CN")
        ctx.citation_metrics["english_available"] = 3

        assert ctx.citation_metrics["english_available"] == 3
