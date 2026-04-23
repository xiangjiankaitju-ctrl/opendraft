#!/usr/bin/env python3
"""Tests for output auto-fix pipeline stage."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

from phases.context import DraftContext
from utils.output_auto_fix import auto_fix_context_outputs


def test_auto_fix_cleans_residue_and_citation_wrapper():
    ctx = DraftContext()
    ctx.topic = "AI productivity"
    ctx.intro_output = "# Introduction\n[VERIFY] Evidence {(Smith, 2024)}\n[MISSING: source]"
    ctx.body_output = "# Body\nNote: The table and content have been crafted to meet the word count and citation requirements."
    ctx.conclusion_output = "# Conclusion\nDone"

    report = auto_fix_context_outputs(ctx)

    assert report["residue_cleaned"] >= 1
    combined = f"{ctx.intro_output}\n{ctx.body_output}\n{ctx.conclusion_output}"
    assert "[VERIFY]" not in combined
    assert "[MISSING:" not in combined
    assert "crafted to meet the word count" not in combined.lower()
    assert "{(Smith, 2024)}" not in combined
    assert "(Smith, 2024)" in combined


def test_auto_fix_renumbers_headings_and_captions():
    ctx = DraftContext()
    ctx.topic = "AI productivity"
    ctx.intro_output = "# 3. Introduction\nText"
    ctx.body_output = "## 4.1 Methods\nTable 1: A\nTable 1: B\nFigure 1: X\nFigure 1: Y"
    ctx.conclusion_output = "# 9. Conclusion\nEnd"

    report = auto_fix_context_outputs(ctx)

    assert report["heading_renumbered"] >= 1
    assert report["caption_renumbered"] >= 1
    assert "Table 1: A" in ctx.body_output
    assert "Table 2: B" in ctx.body_output
    assert "Figure 1: X" in ctx.body_output
    assert "Figure 2: Y" in ctx.body_output


def test_auto_fix_downgrades_unsupported_method_claims_and_augments_topic_alignment():
    ctx = DraftContext()
    ctx.topic = "Research on the Impact of Artificial Intelligence on New Quality Productivity"
    ctx.intro_output = "# Introduction\nGeneric opening without key topic framing."
    ctx.body_output = (
        "# Main Body\n"
        "This study uses a mixed-methods approach with interviews and regression analysis."
    )
    ctx.conclusion_output = "# Conclusion\nDone"

    report = auto_fix_context_outputs(ctx)

    assert report["method_claims_downgraded"] >= 1
    assert report["topic_alignment_augmented"] >= 1
    assert "literature-based conceptual synthesis" in ctx.body_output.lower()
    assert "Concept alignment note:" in ctx.intro_output
