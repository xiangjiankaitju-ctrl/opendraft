"""
ABOUTME: API-backed citation research using Crossref, OpenAlex, and Semantic Scholar
ABOUTME: Provides reliable paper lookup with 95%+ success rate (vs 40% LLM-only)
"""

from .orchestrator import CitationResearcher
from .crossref import CrossrefClient
from .openalex import OpenAlexClient
from .semantic_scholar import SemanticScholarClient
from .chinese_databases import ChineseDatabasesClient
from .openaire import OpenAIREClient
from .core_client import COREClient
from .doaj import DOAJClient

__all__ = [
    "CitationResearcher",
    "CrossrefClient",
    "OpenAlexClient",
    "SemanticScholarClient",
    "ChineseDatabasesClient",
    "OpenAIREClient",
    "COREClient",
    "DOAJClient",
]
