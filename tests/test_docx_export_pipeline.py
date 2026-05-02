#!/usr/bin/env python3
"""Regression tests for production DOCX export formatting pipeline."""

import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_ROOT = PROJECT_ROOT / "engine"
sys.path.insert(0, str(ENGINE_ROOT))

from utils.docx_export_pipeline import preprocess_markdown_for_docx, select_reference_template
from utils.export_professional import export_docx


ZH_SAMPLE = """---
title: 人工智能背景下就业观念变迁及其社会影响研究
language: zh
date: 2026-05-02
---

# Table of Contents

## Abstract

**Research Problem and Approach:** 本文梳理人工智能背景下就业观念变迁。

**Methodology and Findings:** 文献显示就业评价标准发生变化。

**Key Contributions:** 提供社会影响分析框架。

**Implications:** 为教育与劳动政策提供参考。

**Keywords:** 人工智能；就业观念；社会影响

\\newpage

# 1. Introduction

正文段落。

## 1.1 Theoretical Framework

表 9 错误旧编号

| 维度 | 影响路径 |
| --- | --- |
| 技能 | 岗位重组 |

# 5. References

Https://doi.org/10.1000/example
"""


EN_SAMPLE = """---
title: Microstructural Evolution and Performance Control of Metallic Materials in Additive Manufacturing
language: en
date: 2026-05-02
---

## Abstract

**Research Problem and Approach:** This draft reviews metallic materials in additive manufacturing.

**Keywords:** Additive Manufacturing; Laser Powder Bed Fusion

newpage

# 1. Introduction

Cooling rates exceeding $10^6$ K/s influence thermal gradients ($G$), growth velocity ($R$), and the resulting $G/R$ ratio.

Table 8. Old duplicate number

| Microstructural Outcome | Applicable Material Classes | Representative Study | Documented impacts |
| --- | --- | --- | --- |
| Columnar grains | Nickel alloys and steels | Example et al. | Strength anisotropy |

## 1.1 Background

Text.

# References

https://doi.org/10.1000/example
"""


def test_preprocess_chinese_localizes_and_cleans_docx_markdown():
    cleaned, stats = preprocess_markdown_for_docx(ZH_SAMPLE, "zh")

    assert stats.language == "zh"
    assert stats.tables_processed == 1
    assert stats.captions_generated == 1
    assert "Table of Contents" not in cleaned
    assert "Research Problem and Approach" not in cleaned
    assert "研究问题与研究方法" in cleaned
    assert "关键词：" in cleaned
    assert "```{=openxml}" in cleaned
    assert "ewpage" not in cleaned
    assert "\\newpage" not in cleaned
    assert "Https://doi.org" not in cleaned
    assert "https://doi.org/10.1000/example" in cleaned
    assert "表 1" in cleaned
    assert "# 参考文献" in cleaned


def test_preprocess_english_preserves_labels_and_math_readability():
    cleaned, stats = preprocess_markdown_for_docx(EN_SAMPLE, "en")

    assert stats.language == "en"
    assert "Table 1." in cleaned
    assert "10⁶ K/s" in cleaned
    assert "G/R ratio" in cleaned
    assert "Research Problem and Approach" in cleaned
    assert "```{=openxml}" in cleaned
    assert "newpage" not in cleaned


@pytest.mark.parametrize(
    "bad_text",
    [
        "thermal gradients (), growth velocity R",
        "cooling rates exceeding  K/s",
        "the resulting  ratio",
    ],
)
def test_preprocess_blocks_empty_math_variables(bad_text):
    with pytest.raises(ValueError):
        preprocess_markdown_for_docx(f"---\ntitle: t\n---\n\n{bad_text}", "en")


def test_reference_templates_exist():
    assert select_reference_template("zh").exists()
    assert select_reference_template("en").exists()


def test_export_docx_requires_pandoc_and_does_not_call_basic(monkeypatch, tmp_path):
    input_md = tmp_path / "draft.md"
    input_md.write_text(EN_SAMPLE, encoding="utf-8")

    import utils.export_professional as export_module

    original_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda cmd: None if cmd == "pandoc" else original_which(cmd))
    monkeypatch.setattr(
        export_module,
        "export_docx_basic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("basic fallback called")),
    )

    assert export_docx(input_md, tmp_path / "out.docx") is False


def test_export_docx_requires_reference_template(monkeypatch, tmp_path):
    input_md = tmp_path / "draft.md"
    input_md.write_text(EN_SAMPLE, encoding="utf-8")

    import utils.export_professional as export_module

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/pandoc" if cmd == "pandoc" else None)
    monkeypatch.setattr(export_module, "select_reference_template", lambda _lang: tmp_path / "missing.docx")

    assert export_docx(input_md, tmp_path / "out.docx") is False


def _docx_xml(path: Path, name: str = "word/document.xml") -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read(name).decode("utf-8")


def test_docx_post_processor_creates_word_structures(tmp_path):
    docx = pytest.importorskip("docx")
    from utils.docx_post_processor import insert_academic_structure

    output = tmp_path / "sample.docx"
    doc = docx.Document()
    doc.add_heading("摘要", level=1)
    doc.add_paragraph("关键词：人工智能；就业观念")
    doc.add_heading("1. 引言", level=1)
    doc.add_paragraph("正文段落。")
    doc.add_paragraph("表 9  旧表题")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "维度"
    table.cell(0, 1).text = "影响"
    table.cell(1, 0).text = "技能"
    table.cell(1, 1).text = "岗位"
    doc.add_heading("参考文献", level=1)
    doc.add_paragraph("作者. (2025). 题名. https://doi.org/10.1000/example")
    doc.save(output)

    stats = insert_academic_structure(
        output,
        options={"language": "zh", "title": "人工智能背景下就业观念变迁及其社会影响研究"},
    )

    assert stats["tables_processed"] == 1
    assert stats["captions_generated"] == 1

    xml = _docx_xml(output)
    assert "TOC \\o &quot;1-3&quot;" in xml or "TOC \\o \"1-3\"" in xml
    assert 'w:type="page"' in xml
    assert "tblHeader" in xml
    assert "cantSplit" in xml
    assert "tblW" in xml

    processed = docx.Document(output)
    texts = "\n".join(p.text for p in processed.paragraphs)
    assert "目录" in texts
    assert "摘要" in texts
    assert "关键词" in texts
    assert "参考文献" in texts
    assert "Table of Contents" not in texts
    assert "ewpage" not in texts


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc is not installed")
def test_export_docx_generates_real_docx_when_pandoc_available(tmp_path):
    docx = pytest.importorskip("docx")
    input_md = tmp_path / "draft.md"
    output = tmp_path / "draft.docx"
    input_md.write_text(EN_SAMPLE, encoding="utf-8")

    assert export_docx(input_md, output)

    document = docx.Document(output)
    texts = "\n".join(p.text for p in document.paragraphs)
    assert "Table of Contents" in texts
    assert "Abstract" in texts
    assert "Keywords" in texts
    assert "References" in texts
    assert "newpage" not in texts
    assert len(document.tables) == 1
