#!/usr/bin/env python3
"""
ABOUTME: Regression tests for research pipeline relevance, query quality, and robustness guards
ABOUTME: Covers malformed query suppression, relevance scoring, and citation deduplication behavior
"""

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# Add engine directory to path so utils can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))

from utils.api_citations.query_router import QueryRouter
from utils.api_citations.orchestrator import CitationResearcher
from utils.api_citations.semantic_scholar import SemanticScholarClient
import utils.agent_runner as agent_runner
from utils.deep_research import DeepResearchPlanner
from utils.agent_runner import (
    _dedupe_citations,
    _cap_research_queries,
    _rebalance_queries_for_academic_level,
    _prioritize_research_queries,
    _build_quality_rescue_queries,
    _build_research_paper_topup_queries,
    _is_preprint_citation,
    _select_seed_papers,
    _build_research_fallback_queries,
    build_fast_research_queries,
    validate_and_compress_queries,
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
        # policy should be soft-priority reorder, not hard-pruned to only three APIs
        assert any(api in result.api_chain for api in ["openaire", "core"])

    def test_hybrid_router_boosts_method_query_confidence(self):
        router = QueryRouter()

        result = router.classify_and_route("systematic review empirical study on AI productivity in firms")

        assert result.query_type in {"academic", "mixed"}
        assert result.confidence >= 0.45


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
        topic = "城市公共交通服务优化研究"
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
        topic = "urban public transport service optimization"
        metadata = {
            "title": "Urban public transport optimization and service quality improvement",
            "authors": ["Li"],
            "year": 2024,
            "doi": "10.1000/example-doi",
            "journal": "Journal of Transport Policy",
            "abstract": "This paper studies service quality and optimization mechanisms in urban public transport.",
            "source_type": "journal",
        }

        assert researcher._is_topic_result_relevant(topic, metadata) is True
        assert metadata.get("relevance_score", 0.0) >= 0.32

    def test_execution_gate_rejects_low_confidence_generic_query(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        query = "青年社交"
        classification = researcher.query_router.classify_and_route(query)

        worthy, reason = researcher._is_query_execution_worthy(query, classification)

        assert worthy is False
        assert (
            "low-confidence generic query" in reason
            or "underspecified CJK query" in reason
            or "underspecified query" in reason
        )

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

    def test_cjk_method_query_is_not_over_dropped(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        query = "青年数字社交行为实证研究"
        classification = researcher.query_router.classify_and_route(query)

        worthy, reason = researcher._is_query_execution_worthy(query, classification)

        assert worthy is True
        assert reason == ""

    def test_cjk_medium_confidence_chain_avoids_long_tail_timeout_prone_apis(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        query = "数字平台青年社交方式变迁实证研究"
        classification = researcher.query_router.classify_and_route(query)

        chain = researcher._build_quality_aware_api_chain(classification, query)

        if classification.confidence < 0.75:
            assert "openaire" not in chain
            assert "core" not in chain

    def test_multi_result_candidate_search_collects_later_provider_hits(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        researcher.crossref = SimpleNamespace(
            search_papers=lambda _query, limit=5: [
                {
                    "title": "Youth digital platform social interaction and peer relationships",
                    "authors": ["Smith"],
                    "year": 2024,
                    "doi": "10.1000/youth-platforms",
                    "journal": "Journal of Youth Studies",
                    "source_type": "journal",
                },
                {
                    "title": "Adolescent social media friendship quality",
                    "authors": ["Jones"],
                    "year": 2023,
                    "doi": "10.1000/friendship-quality",
                    "journal": "Computers in Human Behavior",
                    "source_type": "journal",
                },
            ]
        )

        candidates = researcher._search_api_candidates(
            "crossref",
            "youth digital platforms social interaction",
            limit=5,
        )

        assert len(candidates) == 2
        assert {source for _, source in candidates} == {"Crossref"}
        assert {metadata["doi"] for metadata, _ in candidates} == {
            "10.1000/youth-platforms",
            "10.1000/friendship-quality",
        }


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
        plan = planner.build_structured_fallback_plan("城市公共交通服务优化研究")

        queries = plan.get("queries", [])
        assert queries
        assert len(queries) <= planner.query_budget
        assert all("site:" not in q.lower() for q in queries)
        assert all("author:" not in q.lower() for q in queries)
        assert all("title:" not in q.lower() for q in queries)
        # ensure old domain-coupled suffixes are not injected
        assert not any("区域发展" in q or "产业升级" in q for q in queries)

    def test_llm_query_optimizer_merges_valid_llm_suggestions(self):
        class _Resp:
            text = """{
                \"core_concepts\": [\"城市交通\", \"服务优化\"],
                \"expanded_queries\": [\"城市交通 服务优化 实证研究\"],
                \"bilingual_queries\": [\"urban transport service optimization empirical study\"],
                \"method_queries\": [\"城市交通 服务优化 文献综述\"],
                \"reasoning\": \"ok\"
            }"""

        class _Model:
            def generate_content(self, *_args, **_kwargs):
                return _Resp()

        planner = DeepResearchPlanner(llm_model=_Model(), min_sources=20, verbose=False)
        queries = planner.optimize_queries_for_retrieval(
            topic="城市公共交通服务优化研究",
            queries=["城市公共交通服务优化 实证研究"],
        )

        assert any("城市交通" in q or "公共交通" in q for q in queries)
        assert any("urban transport" in q.lower() for q in queries)

    def test_llm_query_optimizer_generates_more_natural_english_bridge_queries(self):
        class _Resp:
            text = """{
                \"core_concepts\": [\"urban transport\", \"service optimization\"],
                \"expanded_queries\": [\"service optimization in urban public transport systems\"],
                \"bilingual_queries\": [\"mechanisms through which timetable design improves urban transport service quality\"],
                \"method_queries\": [\"urban transport service quality empirical study\"],
                \"reasoning\": \"ok\"
            }"""

        class _Model:
            def generate_content(self, *_args, **_kwargs):
                return _Resp()

        planner = DeepResearchPlanner(llm_model=_Model(), min_sources=20, verbose=False)
        queries = planner.optimize_queries_for_retrieval(
            topic="城市公共交通服务优化研究",
            queries=["城市公共交通服务优化 实证研究"],
        )

        assert any("mechanisms through which" in q.lower() for q in queries)
        assert not any("urban transport service optimization empirical study" == q.lower() for q in queries)


class TestPlannerAndSeedExpansionGuards:
    def test_fallback_queries_are_bounded_to_fast_window(self):
        queries = _build_research_fallback_queries("人工智能对大学生就业观念的影响研究")
        assert 1 <= len(queries) <= 8

    def test_seed_selection_prefers_high_relevance_and_caps_to_five(self):
        citations = []
        for idx, score in enumerate([0.9, 0.88, 0.7, 0.66, 0.65, 0.64, 0.5], start=1):
            c = Citation(
                citation_id=str(idx),
                authors=["A"],
                year=2024,
                title=f"Paper {idx}",
                source_type="journal",
                doi=f"10.1000/{idx}",
            )
            c.relevance_score = score
            citations.append(c)

        seeds = _select_seed_papers(citations)
        assert len(seeds) == 5
        assert all(getattr(c, "relevance_score", 0.0) >= 0.65 for c in seeds)


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

    def test_llm_candidate_rescue_can_promote_semantically_relevant_result(self):
        class _Resp:
            text = '{"keep_indices": [0], "reasoning": "keep first"}'

        class _Model:
            def generate_content(self, *_args, **_kwargs):
                return _Resp()

        researcher = CitationResearcher(llm_model=_Model(), enable_llm_fallback=False, verbose=False)
        results = [
            ({"title": "Digital platform youth interaction study", "doi": "10.1/c", "relevance_score": 0.12}, "Crossref"),
            ({"title": "Agricultural irrigation mechanisms", "doi": "10.1/d", "relevance_score": 0.11}, "OpenAlex"),
        ]

        rescued = researcher._llm_select_relevant_candidates("digital platform youth social interaction", results)

        assert len(rescued) == 1
        assert rescued[0][0]["doi"] == "10.1/c"


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
        topic = "城市公共交通服务优化研究"
        queries = [
            "某机构 城市交通 白皮书 报告",
            "某研究院 城市交通 发展 报告",
            "城市公共交通 服务优化 影响机制 实证研究",
            "城市公共交通 服务优化 系统综述",
            "城市公共交通 服务优化 路径研究",
            "城市公共交通 服务优化 文献综述",
        ]

        capped = _cap_research_queries(queries, topic, parallel_workers=2)

        # Academic-intent Chinese queries should survive prioritization
        assert "城市公共交通 服务优化 影响机制 实证研究" in capped
        assert "城市公共交通 服务优化 系统综述" in capped
        # Should avoid mechanical "完整题目+方法词" style anchors
        assert not any(q == f"{topic} 系统综述" for q in capped)
        assert not any(q == f"{topic} 文献综述" for q in capped)

    def test_prioritize_queries_keeps_front_half_at_least_half_chinese(self):
        topic = "城市公共交通服务优化研究"
        queries = [
            "urban public transport optimization empirical study",
            "urban transit timetable design case study",
            "service quality evaluation in metropolitan transport",
            "城市公共交通 服务优化 实证研究",
            "城市公共交通 服务质量 案例研究",
            "城市轨道交通 运营优化 比较研究",
            "城市公交 调度优化 文献综述",
            "公共交通 服务质量 提升 机制研究",
        ]

        prioritized = _prioritize_research_queries(queries, topic, "research_paper", parallel_workers=4)
        front_half = prioritized[: max(1, len(prioritized) // 2)]
        zh_count = sum(1 for q in front_half if any('\u4e00' <= ch <= '\u9fff' for ch in q))
        assert zh_count >= max(1, len(front_half) // 2)

    def test_cap_queries_preserves_english_bridge_queries_for_chinese_topic(self):
        topic = "城市公共交通服务优化研究"
        queries = [
            "城市公共交通服务优化 实证研究",
            "城市公共交通服务优化 文献综述",
            "城市公共交通服务优化 机制研究",
            "urban public transport service optimization empirical study",
            "service quality improvement in urban transit literature review",
            "metropolitan bus scheduling and rider experience",
            "urban transport service optimization case study",
        ]

        capped = _cap_research_queries(queries, topic, parallel_workers=4)
        front_half = capped[: max(1, len(capped) // 2)]
        en_count = sum(1 for q in front_half if any('a' <= ch.lower() <= 'z' for ch in q) and not any('\u4e00' <= ch <= '\u9fff' for ch in q))

        assert any("urban" in q.lower() and "transport" in q.lower() for q in capped)
        assert en_count >= 1


class TestRescueQueryPurity:
    def test_chinese_quality_rescue_queries_are_pure_chinese(self):
        queries = _build_quality_rescue_queries(
            "城市公共交通服务优化研究",
            is_chinese_topic=True,
        )
        assert queries
        assert all(any('\u4e00' <= ch <= '\u9fff' for ch in q) for q in queries)
        assert all("empirical" not in q.lower() for q in queries)

    def test_english_quality_rescue_queries_are_pure_english(self):
        queries = _build_quality_rescue_queries(
            "urban public transport service optimization",
            is_chinese_topic=False,
        )
        assert queries
        assert all(not any('\u4e00' <= ch <= '\u9fff' for ch in q) for q in queries)

    def test_research_paper_topup_queries_include_youth_digital_platform_anchors(self):
        queries = _build_research_paper_topup_queries(
            "A Study on the Changes in Youth Social Interaction and Their Impacts in the Era of Digital Platforms"
        )

        joined = " | ".join(q.lower() for q in queries)
        assert queries
        assert "youth digital platforms social interaction" in joined
        assert "adolescent social media friendship quality" in joined
        assert "social media adolescent loneliness systematic review" in joined


class TestWeakQueryRewriteRegenerate:
    def test_low_quality_query_gets_regenerated_not_dropped_if_recoverable(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        bad_query = "城市公共交通服务优化的作用方式"
        classification = researcher.query_router.classify_and_route(bad_query)
        regenerated = researcher._regenerate_query_from_hint(bad_query, classification)

        assert regenerated
        assert any(term in regenerated for term in ["城市公共交通", "服务优化", "研究对象", "影响因素"])


class TestFastPlannerZhConstraints:
    def test_fast_planner_zh_queries_meet_structural_constraints(self):
        topic = "人工智能背景下青年就业观念变迁及其社会影响"
        queries = build_fast_research_queries(topic)
        zh_queries = [q for q in queries if any("\u4e00" <= ch <= "\u9fff" for ch in q)]

        population_markers = {"青年", "大学生", "高校毕业生", "劳动者", "求职者", "学生", "居民", "企业员工"}
        outcome_markers = {"就业意愿", "职业选择", "就业预期", "就业焦虑", "职业价值观", "行为意向", "满意度", "认知"}
        relation_markers = {"影响机制", "作用机制", "中介机制", "调节效应", "社会影响", "机制", "影响"}

        assert 1 <= len(zh_queries) <= 6
        assert all(len([t for t in q.split(" ") if t.strip()]) >= 2 for q in zh_queries)
        assert sum(1 for q in zh_queries if any(m in q for m in population_markers)) >= 2
        assert sum(1 for q in zh_queries if any(m in q for m in outcome_markers)) >= 2
        assert sum(1 for q in zh_queries if any(m in q for m in relation_markers)) >= 1

    def test_fast_planner_zh_generates_broad_population_when_missing_in_topic(self):
        topic = "数字平台治理机制与社会效应研究"
        queries = build_fast_research_queries(topic)
        zh_queries = [q for q in queries if any("\u4e00" <= ch <= "\u9fff" for ch in q)]
        population_markers = ["青年", "劳动者", "求职者", "高校毕业生"]
        covered = {m for m in population_markers if any(m in q for q in zh_queries)}
        assert len(covered) >= 2


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

    def test_research_paper_query_cap_defaults_to_16(self, monkeypatch):
        monkeypatch.delenv("SCOUT_RESEARCH_PAPER_QUERY_CAP", raising=False)
        queries = [f"peer-reviewed empirical query {idx}" for idx in range(40)]

        prioritized = _prioritize_research_queries(
            queries,
            topic="AI productivity in firms",
            academic_level="research_paper",
            parallel_workers=4,
        )

        assert len(prioritized) == 16


class TestResearchRuntimeBudgets:
    @staticmethod
    def _citation(idx: int) -> Citation:
        return Citation(
            citation_id=f"c{idx}",
            authors=["Smith"],
            year=2024,
            title=f"Relevant empirical paper {idx}",
            source_type="journal",
            doi=f"10.1000/test{idx}",
            api_source="Crossref",
        )

    def test_research_paper_degraded_passes_without_compensation_storm(self, monkeypatch, tmp_path):
        calls = []

        class _FakeResearcher:
            def __init__(self, *args, **kwargs):
                self.deadline = None

            def set_deadline(self, deadline_ts):
                self.deadline = deadline_ts

            def capability_matrix(self):
                return {
                    "crossref": {"enabled": True},
                    "openalex": {"enabled": True},
                    "semantic_scholar": {"enabled": True, "cooled_down": False},
                    "openaire": {"enabled": True},
                    "core": {"enabled": True},
                    "doaj": {"enabled": True},
                }

            def research_citation(self, query):
                calls.append(query)
                if len(calls) <= 5:
                    return [TestResearchRuntimeBudgets._citation(len(calls))]
                return []

            def get_metrics_snapshot(self):
                return {
                    "retrievability_ready": True,
                    "accepted_rate": 0.8,
                    "relevance_pass_rate": 0.5,
                    "semantic_acceptance_rate": 0.5,
                }

        monkeypatch.setattr(agent_runner, "CitationResearcher", _FakeResearcher)
        monkeypatch.setattr(
            agent_runner,
            "get_concurrency_config",
            lambda verbose=False: SimpleNamespace(scout_batch_size=10, scout_batch_delay=0, scout_parallel_workers=1),
        )

        result = agent_runner.research_citations_via_api(
            model=object(),
            research_topics=[f"query {idx}" for idx in range(10)],
            output_path=tmp_path / "scout.md",
            target_minimum=10,
            academic_level="research_paper",
            verbose=False,
            use_deep_research=False,
        )

        assert result["count"] == 5
        # Top-up is allowed after weak count, but it should stay bounded rather
        # than creating an unbounded compensation storm.
        assert len(calls) <= 18

    def test_research_paper_topup_reaches_minimal_threshold(self, monkeypatch, tmp_path):
        calls = []

        class _FakeResearcher:
            def __init__(self, *args, **kwargs):
                pass

            def set_deadline(self, deadline_ts):
                pass

            def capability_matrix(self):
                return {
                    "crossref": {"enabled": True},
                    "openalex": {"enabled": True},
                    "semantic_scholar": {"enabled": False, "cooled_down": False},
                    "openaire": {"enabled": True},
                    "core": {"enabled": True},
                    "doaj": {"enabled": True},
                }

            def research_citation(self, query):
                calls.append(query)
                # First pass yields too few sources; focused top-up supplies the
                # remaining citations needed for the minimal threshold.
                if len(calls) <= 3:
                    return [TestResearchRuntimeBudgets._citation(len(calls))]
                if "adolescent" in query.lower() or "youth digital platforms" in query.lower():
                    return [TestResearchRuntimeBudgets._citation(len(calls))]
                return []

            def get_metrics_snapshot(self):
                return {
                    "retrievability_ready": True,
                    "accepted_rate": 0.8,
                    "relevance_pass_rate": 0.8,
                    "semantic_acceptance_rate": 0.8,
                }

        monkeypatch.setattr(agent_runner, "CitationResearcher", _FakeResearcher)
        monkeypatch.setattr(
            agent_runner,
            "get_concurrency_config",
            lambda verbose=False: SimpleNamespace(scout_batch_size=10, scout_batch_delay=0, scout_parallel_workers=1),
        )

        result = agent_runner.research_citations_via_api(
            model=object(),
            research_topics=[f"initial query {idx}" for idx in range(5)],
            output_path=tmp_path / "scout.md",
            target_minimum=10,
            academic_level="research_paper",
            topic="A Study on the Changes in Youth Social Interaction and Their Impacts in the Era of Digital Platforms",
            verbose=False,
            use_deep_research=False,
        )

        assert result["count"] >= 8
        assert any("adolescent" in call.lower() or "youth digital platforms" in call.lower() for call in calls[5:])


class TestZhEnQueryPlanningGuards:
    def test_chinese_title_does_not_generate_mechanical_title_plus_method_queries(self):
        topic = "人工智能背景下就业观念变迁及其社会影响研究"
        queries = build_fast_research_queries(topic)
        assert not any(q == f"{topic} 实证研究" for q in queries)
        assert not any(q == f"{topic} 文献综述" for q in queries)

    def test_chinese_title_has_minimum_chinese_queries(self):
        topic = "人工智能背景下就业观念变迁及其社会影响研究"
        queries = build_fast_research_queries(topic)
        zh_query_count = sum(1 for q in queries if any("\u4e00" <= ch <= "\u9fff" for ch in q))
        assert zh_query_count >= 3

    def test_chinese_title_has_valid_english_supplement_queries(self):
        topic = "人工智能背景下就业观念变迁及其社会影响研究"
        queries = build_fast_research_queries(topic)
        english_queries = [q for q in queries if not any("\u4e00" <= ch <= "\u9fff" for ch in q)]
        assert len(english_queries) >= 1
        assert all(not any("\u4e00" <= ch <= "\u9fff" for ch in q) for q in english_queries)

    def test_english_title_generates_only_english_queries(self):
        topic = "The Impact of Artificial Intelligence on Employment Attitudes among College Students"
        queries = build_fast_research_queries(topic)
        assert queries
        assert all(not any("\u4e00" <= ch <= "\u9fff" for ch in q) for q in queries)

    def test_validate_compress_removes_mixed_language_pseudo_query(self):
        topic = "人工智能背景下就业观念变迁及其社会影响研究"
        mixed = f"{topic} empirical study"
        out = validate_and_compress_queries(topic=topic, queries=[mixed], input_language="zh", mode="fast")
        assert mixed not in out

    def test_validate_compress_removes_duplicate_topic_stitch_query(self):
        topic = "人工智能背景下就业观念变迁及其社会影响研究"
        bad = f"{topic} {topic} 实证研究"
        out = validate_and_compress_queries(topic=topic, queries=[bad], input_language="zh", mode="fast")
        assert bad not in out

    def test_global_deadline_does_not_wait_for_unfinished_parallel_topics(self, monkeypatch, tmp_path):
        class _SlowResearcher:
            def __init__(self, *args, **kwargs):
                pass

            def set_deadline(self, deadline_ts):
                pass

            def capability_matrix(self):
                return {
                    "crossref": {"enabled": True},
                    "openalex": {"enabled": True},
                    "semantic_scholar": {"enabled": False, "cooled_down": False},
                    "openaire": {"enabled": False},
                    "core": {"enabled": False},
                    "doaj": {"enabled": False},
                }

            def research_citation(self, query):
                time.sleep(2.0)
                return []

            def get_metrics_snapshot(self):
                return {"retrievability_ready": False, "accepted_rate": 0.0, "relevance_pass_rate": 0.0}

        monkeypatch.setenv("SCOUT_TOTAL_TIMEOUT_SECONDS", "5")
        monkeypatch.setattr(agent_runner, "CitationResearcher", _SlowResearcher)
        monkeypatch.setattr(
            agent_runner,
            "get_concurrency_config",
            lambda verbose=False: SimpleNamespace(scout_batch_size=4, scout_batch_delay=0, scout_parallel_workers=2),
        )

        start = time.monotonic()
        with pytest.raises(ValueError, match="retrievability gate failed"):
            agent_runner.research_citations_via_api(
                model=object(),
                research_topics=["slow query 1", "slow query 2", "slow query 3", "slow query 4"],
                output_path=tmp_path / "scout.md",
                target_minimum=10,
                academic_level="research_paper",
                verbose=False,
                use_deep_research=False,
                per_topic_timeout_seconds=1,
            )
        elapsed = time.monotonic() - start

        assert elapsed < 1.8


class TestSemanticScholarFastFail:
    def test_semantic_scholar_429_returns_without_sleeping(self, monkeypatch):
        class _Resp:
            status_code = 429
            text = "rate limited"

        client = SemanticScholarClient(timeout=10, max_retries=5)
        client.session.request = lambda *args, **kwargs: _Resp()
        sleeps = []
        monkeypatch.setattr("utils.api_citations.base.time.sleep", lambda seconds: sleeps.append(seconds))

        start = time.monotonic()
        result = client.search_paper("artificial intelligence productivity")
        elapsed = time.monotonic() - start

        assert result is None
        assert sleeps == []
        assert elapsed < 0.2


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
