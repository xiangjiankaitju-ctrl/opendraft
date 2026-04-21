#!/usr/bin/env python3
"""Tests for Chinese citation fallback policy (CNKI -> Baidu Scholar)."""

import os
import sys
import types


# Add engine directory to import path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))

from utils.api_citations.chinese_databases import ChineseDatabasesClient
from utils.agent_runner import _count_chinese_primary_hits
from utils.citation_database import Citation


def _build_test_client(fake_search):
    """Create ChineseDatabasesClient with mocked _search_once."""
    client = ChineseDatabasesClient.__new__(ChineseDatabasesClient)
    client.timeout = 15
    client._serper = object()
    client._dataforseo = None
    client._provider_name = "Mock"
    client._search_once = types.MethodType(lambda self, q: fake_search(q), client)
    return client


def test_chinese_databases_fallback_cnki_to_baidu():
    """When CNKI has no hit, fallback should return Baidu Scholar hit."""
    calls = []

    def fake_search(query):
        calls.append(query)
        if "site:xueshu.baidu.com" in query:
            return {
                "title": "人工智能与新质生产力",
                "url": "https://xueshu.baidu.com/usercenter/paper/show?paperid=test",
                "authors": ["张三"],
                "year": 2024,
            }
        return None

    client = _build_test_client(fake_search)
    result = client.search_paper("人工智能 新质生产力")

    assert result is not None
    assert result.get("publisher") == "Baidu Scholar"
    assert result.get("source_site") == "xueshu.baidu.com"
    # Ensure CNKI was attempted first
    assert calls[0].startswith("site:cnki.net")


def test_count_chinese_primary_hits_detects_cnki_and_baidu():
    """Primary hit counter should identify CNKI and Baidu Scholar by URL/publisher."""
    citations = [
        Citation(
            citation_id="1",
            authors=["A"],
            year=2023,
            title="T1",
            source_type="journal",
            publisher="CNKI",
            url="https://kns.cnki.net/kcms/detail/detail.aspx",
            api_source="Chinese Databases",
        ),
        Citation(
            citation_id="2",
            authors=["B"],
            year=2024,
            title="T2",
            source_type="journal",
            publisher="Baidu Scholar",
            url="https://xueshu.baidu.com/usercenter/paper/show?paperid=abc",
            api_source="Chinese Databases",
        ),
    ]

    counts = _count_chinese_primary_hits(citations)
    assert counts["cnki_hits"] == 1
    assert counts["baidu_scholar_hits"] == 1
    assert counts["primary_hits"] == 2
