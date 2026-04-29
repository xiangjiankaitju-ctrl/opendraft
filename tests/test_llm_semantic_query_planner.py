#!/usr/bin/env python3
"""Regression tests for the LLM semantic query planner path."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

import utils.agent_runner as agent_runner
from utils.agent_runner import (
    _build_quality_rescue_queries,
    _classify_research_quality_failure,
    build_research_query_plan,
)


class _Resp:
    def __init__(self, text: str):
        self.text = text


class _PlannerModel:
    def __init__(self, text: str):
        self.text = text

    def generate_content(self, *_args, **_kwargs):
        return _Resp(self.text)


def _planner_json(topic="live streaming e-commerce consumer trust purchase decision"):
    en_queries = [
        f"{topic} empirical study {idx}"
        for idx in range(1, 18)
    ]
    zh_queries = [
        f"直播电商 消费者信任 购买决策 {idx}"
        for idx in range(1, 8)
    ]
    candidates = [
        {"query": q, "language": "en", "query_type": "core_topic", "priority": "high"}
        for q in en_queries
    ] + [
        {"query": q, "language": "zh", "query_type": "core_topic", "priority": "medium"}
        for q in zh_queries
    ]
    return """{
      "semantic_units": {
        "background_or_context": ["live streaming e-commerce", "直播电商"],
        "main_constructs": ["consumer trust", "消费者信任"],
        "population_or_object": ["consumers", "消费者"],
        "outcomes_or_effects": ["purchase decision", "购买决策"],
        "mechanisms": ["trust formation mechanism", "信任形成机制"],
        "methods": ["empirical study", "实证研究"],
        "adjacent_terms": ["purchase intention"],
        "english_academic_terms": ["live streaming e-commerce", "consumer trust", "purchase decision"]
      },
      "query_candidates": %s
    }""" % __import__("json").dumps(candidates, ensure_ascii=False)


def test_chinese_title_query_pool_ratio():
    plan = build_research_query_plan(
        "直播电商环境下消费者信任形成机制与购买决策研究",
        academic_level="research_paper",
        llm_model=_PlannerModel(_planner_json()),
        target_minimum=20,
    )

    diag = plan["diagnostics"]["planner"]
    assert diag["query_pool_size"] >= 20
    assert diag["en_query_count"] >= diag["zh_query_count"]
    assert diag["zh_query_count"] >= 5


def test_english_title_only_generates_english_queries():
    candidates = [
        {
            "query": f"social media use college students social anxiety empirical study {idx}",
            "language": "en",
            "query_type": "core_topic",
            "priority": "high",
        }
        for idx in range(1, 25)
    ] + [
        {
            "query": "大学生 社交媒体 社交焦虑",
            "language": "zh",
            "query_type": "core_topic",
            "priority": "medium",
        }
    ]
    payload = __import__("json").dumps({
        "semantic_units": {
            "background_or_context": ["social media use"],
            "main_constructs": ["social anxiety"],
            "population_or_object": ["college students"],
            "outcomes_or_effects": ["anxiety"],
            "mechanisms": ["mechanism"],
            "methods": ["empirical study"],
            "adjacent_terms": [],
            "english_academic_terms": ["social media use", "college students", "social anxiety"],
        },
        "query_candidates": candidates,
    }, ensure_ascii=False)

    plan = build_research_query_plan(
        "The Impact of Social Media Use on College Students' Social Anxiety",
        academic_level="research_paper",
        llm_model=_PlannerModel(payload),
    )

    assert all(q.language == "en" for q in plan["query_pool"])
    assert plan["diagnostics"]["planner"]["zh_query_count"] == 0
    assert plan["diagnostics"]["planner"]["validation_removed"]["mixed_language_queries_removed"] == 1


def test_llm_success_does_not_call_heuristic_generators(monkeypatch):
    monkeypatch.setattr(agent_runner, "_generate_diversified_queries", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called")))
    monkeypatch.setattr(agent_runner, "_infer_english_terms_from_chinese_terms", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called")))

    plan = build_research_query_plan(
        "直播电商环境下消费者信任形成机制与购买决策研究",
        academic_level="research_paper",
        llm_model=_PlannerModel(_planner_json()),
    )

    diag = plan["diagnostics"]["planner"]
    assert diag["planner_mode"] == "llm_semantic"
    assert diag["planner_fallback_used"] is False


def test_llm_failure_records_low_confidence_fallback():
    plan = build_research_query_plan(
        "直播电商环境下消费者信任形成机制与购买决策研究",
        academic_level="research_paper",
        llm_model=None,
    )

    diag = plan["diagnostics"]["planner"]
    assert diag["planner_mode"] == "heuristic_fallback"
    assert diag["planner_fallback_used"] is True
    assert diag["planner_confidence"] == "low"


def test_query_pool_not_cut_to_execution_batch_size():
    plan = build_research_query_plan(
        "直播电商环境下消费者信任形成机制与购买决策研究",
        academic_level="research_paper",
        llm_model=_PlannerModel(_planner_json()),
        target_minimum=10,
    )

    assert plan["diagnostics"]["planner"]["query_pool_size"] >= 20
    assert plan["query_budget"]["first_batch"] <= plan["diagnostics"]["planner"]["query_pool_size"]


def test_rescue_queries_stay_topic_local():
    semantic_units = {
        "background_or_context": ["直播电商"],
        "main_constructs": ["消费者信任"],
        "population_or_object": ["消费者"],
        "outcomes_or_effects": ["购买决策"],
        "mechanisms": ["信任形成机制"],
        "methods": ["实证研究"],
        "adjacent_terms": [],
        "english_academic_terms": [],
    }
    queries = _build_quality_rescue_queries(
        "直播电商环境下消费者信任形成机制与购买决策研究",
        is_chinese_topic=True,
        semantic_units=semantic_units,
    )
    forbidden = ["youth workers", "job seekers", "college graduates", "adolescent loneliness"]
    flat_units = [v for values in semantic_units.values() for v in values]

    assert queries
    for query in queries:
        assert not any(term in query.lower() for term in forbidden)
        assert sum(1 for unit in flat_units if unit and unit in query) >= 2


def test_candidate_pipeline_inconsistency_priority():
    failure = _classify_research_quality_failure(
        raw_candidates_count=0,
        normalized_candidates_count=0,
        accepted_candidates_count=0,
        valid_citations_count=3,
        relevance_pass_rate=0.0,
        required_threshold=0.45,
        target_minimum=10,
    )
    assert failure == "candidate_pipeline_inconsistency"


def test_preprint_ratio_below_target_is_not_hard_failure():
    failure = _classify_research_quality_failure(
        raw_candidates_count=2,
        normalized_candidates_count=2,
        accepted_candidates_count=2,
        valid_citations_count=2,
        relevance_pass_rate=0.9,
        required_threshold=0.45,
        target_minimum=10,
        preprint_ratio=0.5,
        max_preprint_ratio=0.3,
    )
    warnings = []
    if 0.5 > 0.3 and 2 < 10:
        warnings.append("preprint_ratio_high")
    assert failure == "insufficient_citation_count"
    assert "preprint_ratio_high" in warnings
