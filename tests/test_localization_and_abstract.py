#!/usr/bin/env python3
"""Regression tests for Chinese-localized templates and abstract placeholder handling."""

import os
import sys
from pathlib import Path


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))

from phases.context import DraftContext
from utils.abstract_generator import replace_placeholder_with_abstract, _measure_abstract_length
from phases.compile import _clean_chinese_final_artifacts
from utils.text_utils import localize_chapter_headings


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


class TestChineseLanguageInstruction:
    def test_draft_context_supports_citation_metrics(self):
        ctx = DraftContext(language="zh-CN")
        ctx.citation_metrics["english_available"] = 3

        assert ctx.citation_metrics["english_available"] == 3
