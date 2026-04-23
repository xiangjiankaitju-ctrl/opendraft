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
from utils.agent_runner import _dedupe_citations, _cap_research_queries
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
