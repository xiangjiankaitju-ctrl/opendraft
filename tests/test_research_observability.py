#!/usr/bin/env python3
"""Observability and explainability regression tests for research pipeline."""

import json
import os
import sys
import tempfile
import uuid


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'engine'))

from utils.api_citations.orchestrator import CitationResearcher


class TestResearchObservability:
    def test_query_skip_writes_trace_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = os.path.join(tmpdir, "research_trace.jsonl")
            os.environ["RESEARCH_TRACE_PATH"] = trace_path

            researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
            researcher.cache = {}
            unique_query = f"artificial intelligence download torrent movie {uuid.uuid4().hex}"
            researcher.research_citation(unique_query)

            assert os.path.exists(trace_path)
            lines = [json.loads(line) for line in open(trace_path, "r", encoding="utf-8") if line.strip()]
            assert any(line.get("event") == "query_skipped" for line in lines)

    def test_metrics_snapshot_contains_quality_fields(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        researcher.metrics["candidates_seen"] = 5
        researcher.metrics["candidates_accepted"] = 2
        snapshot = researcher.get_metrics_snapshot()

        assert "accepted_rate" in snapshot
        assert "relevance_pass_rate" in snapshot
        assert "semantic_acceptance_rate" in snapshot
        assert snapshot["accepted_rate"] == 0.4
        assert snapshot["relevance_pass_rate"] == 0.4
        assert "provider_health" in snapshot

    def test_metrics_snapshot_uses_semantic_acceptance_when_higher(self):
        researcher = CitationResearcher(enable_llm_fallback=False, verbose=False)
        researcher.metrics["candidates_seen"] = 10
        researcher.metrics["candidates_accepted"] = 2
        researcher.metrics["candidates_semantic_accepted"] = 5

        snapshot = researcher.get_metrics_snapshot()

        assert snapshot["accepted_rate"] == 0.2
        assert snapshot["relevance_pass_rate"] == 0.5
        assert snapshot["semantic_acceptance_rate"] == 0.5
