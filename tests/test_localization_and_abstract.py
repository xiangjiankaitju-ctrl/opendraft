#!/usr/bin/env python3
"""Regression tests for Chinese-localized templates and abstract placeholder handling."""

import os
import sys
from pathlib import Path


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
    _assert_markdown_table_rows_not_reduced,
    _prepare_body_section,
)
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
        updated = replace_placeholder_with_abstract(draft, "这是一个符合要求的中文摘要。", "chinese")

        assert "这是一个符合要求的中文摘要。" in updated
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
        assert "**研究问题与研究方法:** 中文摘要。" in text
        assert "ewpage" not in [line.strip() for line in text.splitlines()]
        assert "\\newpage" in text
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


class TestChineseLanguageInstruction:
    def test_draft_context_supports_citation_metrics(self):
        ctx = DraftContext(language="zh-CN")
        ctx.citation_metrics["english_available"] = 3

        assert ctx.citation_metrics["english_available"] == 3
