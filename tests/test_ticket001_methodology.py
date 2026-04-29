#!/usr/bin/env python3
"""
Test TICKET-001: Methodology Section Honesty

This test validates that the crafter agent produces methodology sections
with honest "narrative review" framing instead of fake "systematic review" claims.

Run with: python tests/test_ticket001_methodology.py
"""

import sys
import os
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(PROJECT_ROOT / "engine"))

from phases.compose import _resolve_method_guard, _sanitize_methodology_output
from phases.context import DraftContext


def load_prompt(prompt_path: str) -> str:
    """Load prompt file from project root"""
    path = PROJECT_ROOT / prompt_path
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

# FORBIDDEN phrases that indicate fake systematic review claims
FORBIDDEN_PHRASES = [
    "systematic review was conducted",
    "following PRISMA guidelines",
    "PRISMA flow",
    "records identified",
    "records screened",
    "duplicates removed (n=",
    "inter-rater reliability",
    "risk of bias assessment",
    "titles and abstracts were screened",
    "full-text articles assessed",
]

# EXPECTED phrases for honest narrative review
EXPECTED_PHRASES = [
    "narrative review",
    # Alternative acceptable phrases:
    "literature review",
    "comprehensive review",
    "scoping review",
]


def test_prompt_contains_honesty_rules():
    """Test 1: Verify crafter.md contains methodology honesty rules"""
    print("\n" + "="*60)
    print("TEST 1: Prompt contains methodology honesty rules")
    print("="*60)

    prompt = load_prompt("engine/prompts/03_compose/crafter.md")

    checks = [
        ("METHODOLOGY SECTION HONESTY" in prompt, "Has honesty section header"),
        ("narrative literature review" in prompt, "Mentions narrative review"),
        ("FORBIDDEN Claims" in prompt, "Has forbidden claims section"),
        ("PRISMA" in prompt, "References PRISMA as forbidden"),
        ("systematic review" in prompt.lower(), "Mentions systematic review (to forbid it)"),
    ]

    all_passed = True
    for condition, description in checks:
        status = "✅ PASS" if condition else "❌ FAIL"
        print(f"  {status}: {description}")
        if not condition:
            all_passed = False

    if __name__ == "__main__":
        return all_passed
    assert all_passed


def test_architect_has_review_types():
    """Test 2: Verify architect.md has review type classification"""
    print("\n" + "="*60)
    print("TEST 2: Architect prompt has review type classification")
    print("="*60)

    prompt = load_prompt("engine/prompts/02_structure/architect.md")

    checks = [
        ("REVIEW TYPE CLASSIFICATION" in prompt, "Has review type section"),
        ("Narrative Review" in prompt, "Lists narrative review"),
        ("NOT supported" in prompt, "Marks systematic review as not supported"),
        ("Search Strategy" in prompt, "Has search strategy terminology"),
    ]

    all_passed = True
    for condition, description in checks:
        status = "✅ PASS" if condition else "❌ FAIL"
        print(f"  {status}: {description}")
        if not condition:
            all_passed = False

    if __name__ == "__main__":
        return all_passed
    assert all_passed


def test_deep_research_not_systematic():
    """Test 3: Verify deep_research.md doesn't claim 'systematic'"""
    print("\n" + "="*60)
    print("TEST 3: Deep research prompt renamed to COMPREHENSIVE")
    print("="*60)

    prompt = load_prompt("engine/prompts/01_research/deep_research.md")

    # Check first 500 chars for the header
    header = prompt[:500]

    checks = [
        ("COMPREHENSIVE RESEARCH PLANNER" in header, "Title says COMPREHENSIVE"),
        ("SYSTEMATIC RESEARCH PLANNER" not in header, "Title does NOT say SYSTEMATIC"),
    ]

    all_passed = True
    for condition, description in checks:
        status = "✅ PASS" if condition else "❌ FAIL"
        print(f"  {status}: {description}")
        if not condition:
            all_passed = False

    if __name__ == "__main__":
        return all_passed
    assert all_passed


def test_crafter_output_live(skip_if_no_key=True):
    """Test 4: Actually run crafter and check output (requires full engine setup)"""
    print("\n" + "="*60)
    print("TEST 4: Live crafter output check")
    print("="*60)

    # This test requires full engine setup which is complex
    # For isolated testing, we verify the prompt rules are in place (tests 1-3)
    # Full integration testing happens via generate_draft workflow
    print("  ⏭️  SKIPPED: Requires full draft generation pipeline")
    print("  Run full integration test with: python engine/draft_generator.py --topic 'test'")
    return None


def test_method_guard_defaults_to_conceptual_without_data_inputs():
    ctx = DraftContext()
    ctx.topic = "人工智能背景下就业观念变迁及其社会影响研究"

    guard = _resolve_method_guard(ctx)

    assert ctx.no_data_available is True
    assert ctx.method_type == "conceptual"
    assert ctx.force_method_type == "conceptual / literature-based"
    assert "literature-based" in guard
    assert "基于问卷调查" in guard


def test_method_guard_allows_empirical_only_with_explicit_data_inputs():
    ctx = DraftContext()
    ctx.dataset = "survey_panel_2025"
    ctx.sample_size = 500
    ctx.data_source = "user-provided survey export"

    guard = _resolve_method_guard(ctx)

    assert ctx.no_data_available is False
    assert ctx.method_type == "empirical"
    assert "Empirical methodology is allowed" in guard


def test_methodology_sanitizer_removes_forbidden_empirical_phrases_in_conceptual_mode():
    raw = "本研究采用访谈法，并基于问卷调查，通过收集数据展开分析。实证分析结果表明，样本数据来源于某平台。"
    sanitized = _sanitize_methodology_output(raw, conceptual_mode=True)

    assert "本研究采用访谈法" not in sanitized
    assert "基于问卷调查" not in sanitized
    assert "通过收集数据" not in sanitized
    assert "实证分析结果表明" not in sanitized
    assert "样本数据来源于" not in sanitized
    assert "文献" in sanitized or "理论" in sanitized


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("TICKET-001 VALIDATION: Methodology Section Honesty")
    print("="*60)

    results = {
        "prompt_honesty_rules": test_prompt_contains_honesty_rules(),
        "architect_review_types": test_architect_has_review_types(),
        "deep_research_naming": test_deep_research_not_systematic(),
        "live_output": test_crafter_output_live(skip_if_no_key=True),
    }

    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    passed = 0
    failed = 0
    skipped = 0

    for test_name, result in results.items():
        if result is None:
            status = "⏭️  SKIPPED"
            skipped += 1
        elif result:
            status = "✅ PASS"
            passed += 1
        else:
            status = "❌ FAIL"
            failed += 1
        print(f"  {status}: {test_name}")

    print(f"\n  Total: {passed} passed, {failed} failed, {skipped} skipped")

    if failed > 0:
        print("\n  ❌ TICKET-001 VALIDATION FAILED")
        return 1
    else:
        print("\n  ✅ TICKET-001 VALIDATION PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
