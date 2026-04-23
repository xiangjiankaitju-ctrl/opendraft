#!/usr/bin/env python3
"""Tests for relevance-aware citation top-up selection."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

from phases.citations import _top_up_unique_citations
from utils.citation_database import Citation


def _citation(cite_id: str, title: str, abstract: str = "") -> Citation:
    return Citation(
        citation_id=cite_id,
        authors=["Tester"],
        year=2024,
        title=title,
        source_type="journal",
        abstract=abstract,
        doi=f"10.1234/{cite_id}",
        journal="Journal of Tests",
    )


def test_top_up_prefers_topic_relevant_candidates():
    existing = [_citation("cite_001", "General productivity baseline")]

    relevant = _citation(
        "cite_002",
        "Artificial intelligence and productivity growth in manufacturing",
        abstract="Empirical study on AI-driven total factor productivity improvements.",
    )
    irrelevant = _citation(
        "cite_003",
        "Forest biodiversity monitoring with satellite imagery",
        abstract="Ecology-focused classification workflow.",
    )

    topped = _top_up_unique_citations(
        existing=existing,
        pool=[irrelevant, relevant],
        topic="Research on the Impact of Artificial Intelligence on New Quality Productivity",
        target_count=2,
        verbose=False,
    )

    assert len(topped) == 2
    assert topped[1].title == relevant.title
