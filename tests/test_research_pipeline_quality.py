#!/usr/bin/env python3
"""
ABOUTME: Regression tests for research pipeline relevance, query quality, and robustness guards
ABOUTME: Covers malformed query suppression, relevance scoring, and citation deduplication behavior
"""

import os
import sys


# Add engine directory to path so utils can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))

from utils.api_citations.query_router import QueryRouter
from utils.api_citations.orchestrator import CitationResearcher
from utils.deep_research import DeepResearchPlanner
from utils.agent_runner import (
    _dedupe_citations,
    _cap_research_queries,
    _rebalance_queries_for_academic_level,
    _prioritize_research_queries,
    _build_quality_rescue_queries,
    _is_preprint_citation,
)
from utils.citation_database import Citation


class TestQueryRouterQuality:
    def test_rejects_malformed_mixed_script_query(self):
        router = QueryRouter()

        result = router.classify_and_route(
            "artificial intelligence new质productive forces mechanism research"
        )

        assert result.query_quality == "low"
        assert result.should_query is False
        assert "malformed mixed-script token" in result.rewrite_hint

    def test_policy_query_uses_narrower_budget_chain(self):
        router = QueryRouter()

        result = router.classify_and_route("中国信通院 人工智能白皮书 产业发展")

        assert result.should_query is True
        assert result.query_quality in {"medium", "high"}
        assert result.api_chain[:3] == ["crossref", "openalex", "doaj"]
        assert "semantic_scholar" not in result.api_chain


class TestCitationResearcherQualityFilters:
    def test_rewrite_query_removes_malformed_token(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        classification = researcher.query_router.classify_and_route(
            "artificial intelligence new质productive forces mechanism research"
        )

        rewritten = researcher._rewrite_query_for_execution(
            "artificial intelligence new质productive forces mechanism research",
            classification,
        )

        assert "new质productive" not in rewritten
        assert "artificial intelligence" in rewritten

    def test_relevance_filter_rejects_obviously_off_topic_result(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        topic = "人工智能对新质生产力的影响研究"
        metadata = {
            "title": "Metabolic Management Center: An innovation project for diabetes management",
            "authors": ["Zhang"],
            "year": 2018,
            "doi": "10.1111/1753-0407.12847",
            "journal": "Journal of Diabetes",
            "abstract": "Clinical patient hospital therapy outcomes for diabetes treatment.",
            "source_type": "journal",
        }

        assert researcher._is_topic_result_relevant(topic, metadata) is False
        assert metadata.get("relevance_score", 1.0) < 0.34

    def test_relevance_filter_keeps_aligned_productivity_result(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        topic = "artificial intelligence productivity industrial upgrading"
        metadata = {
            "title": "Artificial intelligence enables industrial upgrading and productivity growth",
            "authors": ["Li"],
            "year": 2024,
            "doi": "10.1000/example-doi",
            "journal": "Journal of Industrial Economics",
            "abstract": "This paper studies artificial intelligence, industrial upgrading, and productivity growth mechanisms.",
            "source_type": "journal",
        }

        assert researcher._is_topic_result_relevant(topic, metadata) is True
        assert metadata.get("relevance_score", 0.0) >= 0.32

    def test_execution_gate_rejects_low_confidence_generic_query(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        query = "数字平台时代青年社交模式"
        classification = researcher.query_router.classify_and_route(query)

        worthy, reason = researcher._is_query_execution_worthy(query, classification)

        assert worthy is False
        assert "low-confidence generic query" in reason

    def test_quality_aware_chain_narrows_medium_confidence_queries(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        query = "人工智能 新质生产力 影响"
        classification = researcher.query_router.classify_and_route(query)

        chain = researcher._build_quality_aware_api_chain(classification, query)

        assert chain
        assert set(chain).issubset({"crossref", "openalex", "doaj", "openaire", "core", "semantic_scholar"})
        # Medium-confidence queries should not default to broad open repository fan-out.
        if classification.query_quality == "medium" and classification.confidence < 0.55:
            assert "openaire" not in chain
            assert "core" not in chain


class TestDeepResearchPlannerBudgeting:
    class _DummyModel:
        def generate_content(self, *_args, **_kwargs):
            raise RuntimeError("not used in unit tests")

    def test_query_budget_is_bounded_and_source_driven(self):
        planner_low = DeepResearchPlanner(llm_model=self._DummyModel(), min_sources=10, verbose=False)
        planner_mid = DeepResearchPlanner(llm_model=self._DummyModel(), min_sources=20, verbose=False)
        planner_high = DeepResearchPlanner(llm_model=self._DummyModel(), min_sources=80, verbose=False)

        assert 20 <= planner_low.query_budget <= 60
        assert 20 <= planner_mid.query_budget <= 60
        assert 20 <= planner_high.query_budget <= 60
        assert planner_high.query_budget >= planner_mid.query_budget >= planner_low.query_budget

    def test_structured_fallback_plan_respects_budget_and_plain_query_syntax(self):
        planner = DeepResearchPlanner(llm_model=self._DummyModel(), min_sources=20, verbose=False)
        plan = planner.build_structured_fallback_plan("人工智能对新质生产力的影响研究")

        queries = plan.get("queries", [])
        assert queries
        assert len(queries) <= planner.query_budget
        assert all("site:" not in q.lower() for q in queries)
        assert all("author:" not in q.lower() for q in queries)
        assert all("title:" not in q.lower() for q in queries)

    def test_llm_query_optimizer_merges_valid_llm_suggestions(self):
        class _Resp:
            text = """{
                \"core_concepts\": [\"数字平台\", \"青年社交\"],
                \"expanded_queries\": [\"数字平台 青年社交 实证研究\"],
                \"bilingual_queries\": [\"digital platforms youth social interaction empirical study\"],
                \"method_queries\": [\"数字平台时代 青年社交 文献综述\"],
                \"reasoning\": \"ok\"
            }"""

        class _Model:
            def generate_content(self, *_args, **_kwargs):
                return _Resp()

        planner = DeepResearchPlanner(llm_model=_Model(), min_sources=20, verbose=False)
        queries = planner.optimize_queries_for_retrieval(
            topic="数字平台时代青年社交方式的变化及其影响研究",
            queries=["数字平台时代青年社交方式变化实证研究"],
        )

        assert any("数字平台" in q for q in queries)
        assert any("digital platforms" in q.lower() for q in queries)


class TestLLMRelevanceReranker:
    def test_llm_reranker_keeps_selected_candidates(self):
        class _Resp:
            text = '{"keep_indices": [1], "reasoning": "candidate 1 is best"}'

        class _Model:
            def generate_content(self, *_args, **_kwargs):
                return _Resp()

        researcher = CitationResearcher(llm_model=_Model(), enable_llm_fallback=False, verbose=False)
        results = [
            ({"title": "Clinical diabetes intervention", "doi": "10.1/a", "relevance_score": 0.35}, "Crossref"),
            ({"title": "Youth social interaction on digital platforms", "doi": "10.1/b", "relevance_score": 0.36}, "OpenAlex"),
        ]

        reranked = researcher._llm_rerank_relevance("digital platform youth social interaction", results)

        assert reranked[0][0]["doi"] == "10.1/b"


class TestCitationDeduplication:
    def test_dedupe_citations_by_doi(self):
        citations = [
            Citation(
                citation_id="1",
                authors=["A"],
                year=2024,
                title="Artificial Intelligence and Productivity",
                source_type="journal",
                doi="10.1000/test-doi",
                api_source="Crossref",
            ),
            Citation(
                citation_id="2",
                authors=["B"],
                year=2024,
                title="Artificial Intelligence and Productivity",
                source_type="journal",
                doi="10.1000/test-doi",
                api_source="OpenAlex",
            ),
        ]

        deduped = _dedupe_citations(citations)

        assert len(deduped) == 1
        assert deduped[0].api_source == "Crossref"


class TestChineseQueryPrioritization:
    def test_cap_queries_prioritizes_academic_intent_for_chinese_topic(self):
        topic = "人工智能对新质生产力的影响研究"
        queries = [
            "中国信通院 人工智能白皮书 技术产业融合",
            "某研究院 人工智能 发展 报告",
            "人工智能 新质生产力 影响机制 实证研究",
            "人工智能 新质生产力 全要素生产率",
            "人工智能 新质生产力 产业升级 路径",
            "人工智能 新质生产力 文献综述",
        ]

        capped = _cap_research_queries(queries, topic, parallel_workers=2)

        # High-signal academic anchors should be injected and retained
        assert f"{topic} 影响机制 实证研究" in capped
        assert f"{topic} 全要素生产率" in capped
        # Academic-intent queries should survive prioritization
        assert "人工智能 新质生产力 影响机制 实证研究" in capped
        assert "人工智能 新质生产力 全要素生产率" in capped

    def test_prioritize_queries_keeps_front_half_at_least_half_chinese(self):
        topic = "人工智能对企业财务管理的影响研究"
        queries = [
            "artificial intelligence corporate financial management empirical study",
            "AI finance function case study",
            "machine learning accounting internal control review",
            "人工智能 企业 财务管理 实证研究",
            "人工智能 财务共享 风险控制",
            "生成式人工智能 财务分析 案例研究",
            "人工智能 预算管理 文献综述",
            "企业 财务管理 内部控制 智能化",
        ]

        prioritized = _prioritize_research_queries(queries, topic, "research_paper", parallel_workers=4)
        front_half = prioritized[: max(1, len(prioritized) // 2)]
        zh_count = sum(1 for q in front_half if any('\u4e00' <= ch <= '\u9fff' for ch in q))
        assert zh_count >= max(1, len(front_half) // 2)


class TestRescueQueryPurity:
    def test_chinese_quality_rescue_queries_are_pure_chinese(self):
        queries = _build_quality_rescue_queries(
            "人工智能对企业财务管理的影响研究",
            is_chinese_topic=True,
        )
        assert queries
        assert all(any('\u4e00' <= ch <= '\u9fff' for ch in q) for q in queries)
        assert all("empirical" not in q.lower() for q in queries)

    def test_english_quality_rescue_queries_are_pure_english(self):
        queries = _build_quality_rescue_queries(
            "artificial intelligence corporate financial management",
            is_chinese_topic=False,
        )
        assert queries
        assert all(not any('\u4e00' <= ch <= '\u9fff' for ch in q) for q in queries)


class TestWeakQueryRewriteRegenerate:
    def test_low_quality_query_gets_regenerated_not_dropped_if_recoverable(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        bad_query = "人工智能对企业财务管理的影响方式"
        classification = researcher.query_router.classify_and_route(bad_query)
        regenerated = researcher._regenerate_query_from_hint(bad_query, classification)

        assert regenerated
        assert any(term in regenerated for term in ["实证研究", "机制研究", "人工智能", "财务管理"])


class TestResearchPaperQueryRebalance:
    def test_rebalance_caps_industry_queries_for_research_paper(self):
        queries = [
            "peer-reviewed studies on AI productivity",
            "systematic review AI manufacturing productivity",
            "meta-analysis AI firm performance",
            "empirical analysis AI total factor productivity",
            "journal paper AI operational efficiency",
            "conference paper AI industrial upgrading",
            "McKinsey report on AI productivity",
            "Gartner analysis AI quality",
            "BCG white paper AI efficiency",
            "OECD framework AI productivity",
        ]

        rebalanced = _rebalance_queries_for_academic_level(
            queries,
            academic_level="research_paper",
            limit=10,
        )

        industry_terms = ("mckinsey", "gartner", "bcg", "oecd", "white paper")
        industry_count = sum(
            1 for q in rebalanced
            if any(term in q.lower() for term in industry_terms)
        )
        assert len(rebalanced) >= 8
        assert industry_count <= 2


class TestPreprintHeuristics:
    def test_detects_ssrn_preprint_by_doi(self):
        citation = Citation(
            citation_id="c1",
            authors=["A"],
            year=2025,
            title="AI and Productivity",
            source_type="journal",
            doi="10.2139/ssrn.1234567",
        )
        assert _is_preprint_citation(citation) is True

    def test_non_preprint_journal_not_flagged(self):
        citation = Citation(
            citation_id="c2",
            authors=["B"],
            year=2024,
            title="Artificial intelligence and firm productivity",
            source_type="journal",
            doi="10.1016/j.frl.2023.104437",
        )
        assert _is_preprint_citation(citation) is False
