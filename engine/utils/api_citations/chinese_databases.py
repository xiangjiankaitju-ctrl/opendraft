#!/usr/bin/env python3
"""
ABOUTME: Chinese academic database search client (CNKI, Wanfang, CQVIP)
ABOUTME: Uses web search providers with site constraints as pragmatic integration
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from .base import normalize_citation_metadata

logger = logging.getLogger(__name__)


class ChineseDatabasesClient:
    """Search Chinese academic databases via constrained web search."""

    CNKI_SITES = [
        ("CNKI", "cnki.net"),
        ("CNKI OverSea", "oversea.cnki.net"),
    ]

    BAIDU_SCHOLAR_SITES = [
        ("Baidu Scholar", "xueshu.baidu.com"),
    ]

    OTHER_CHINESE_SITES = [
        ("Wanfang", "wanfangdata.com.cn"),
        ("CQVIP", "cqvip.com"),
        ("SinoMed", "sinomed.ac.cn"),
        ("NSSD", "nssd.cn"),
        ("NSTL", "nstl.gov.cn"),
        ("Chaoxing", "chaoxing.com"),
        ("Airiti Library", "airitilibrary.com"),
    ]

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self._serper = None
        self._dataforseo = None
        self._provider_name = None
        self._init_provider()

    def _init_provider(self) -> None:
        """Lazy init available web search provider."""
        try:
            from .serper_client import SerperClient
            self._serper = SerperClient(timeout=self.timeout, validate_urls=False, num_results=8)
            self._provider_name = "Serper"
            return
        except Exception:
            self._serper = None

        try:
            from .dataforseo_client import DataForSEOClient
            self._dataforseo = DataForSEOClient(timeout=self.timeout)
            self._provider_name = "DataForSEO"
        except Exception:
            self._dataforseo = None

    def capability_status(self) -> Dict[str, Any]:
        """Expose runtime capability for CNKI/Baidu Scholar retrieval."""
        has_provider = self._serper is not None or self._dataforseo is not None
        return {
            "enabled": has_provider,
            "provider": self._provider_name,
            "supports_cnki": True,
            "supports_baidu_scholar": True,
            "priority": ["CNKI", "Baidu Scholar", "Wanfang", "CQVIP"],
            "fallback_policy": "CNKI -> Baidu Scholar -> Wanfang/CQVIP/other Chinese databases",
        }

    def search_paper(self, query: str) -> Optional[Dict[str, Any]]:
        """Search CNKI first, then Baidu Scholar, then other Chinese databases."""
        # Phase 1: CNKI priority
        hit = self._search_sites(query, self.CNKI_SITES)
        if hit:
            return hit

        # Phase 2: explicit fallback to Baidu Scholar (user-critical fallback path)
        logger.info("CNKI returned no result, falling back to Baidu Scholar")
        hit = self._search_sites(query, self.BAIDU_SCHOLAR_SITES)
        if hit:
            return hit

        # Phase 3: secondary Chinese databases
        return self._search_sites(query, self.OTHER_CHINESE_SITES)

    def _search_sites(self, query: str, sites: list[tuple[str, str]]) -> Optional[Dict[str, Any]]:
        """Search a set of constrained sites and return first valid normalized result."""
        for db_name, site in sites:
            site_query = f"site:{site} {query}"
            raw = self._search_once(site_query)
            if not raw:
                continue

            raw["source_type"] = raw.get("source_type") or "journal"
            normalized = normalize_citation_metadata(raw)
            if not normalized:
                continue

            # Keep source information for downstream observability
            normalized["publisher"] = normalized.get("publisher") or db_name
            normalized["chinese_database"] = db_name
            normalized["source_site"] = site
            if not normalized.get("authors"):
                normalized["authors"] = [db_name]
            if not normalized.get("year"):
                normalized["year"] = datetime.now().year
            if not normalized.get("url"):
                continue
            return normalized
        return None

    def _search_once(self, query: str) -> Optional[Dict[str, Any]]:
        if self._serper is not None:
            return self._serper.search_paper(query)
        if self._dataforseo is not None:
            return self._dataforseo.search_paper(query)
        return None

    def close(self) -> None:
        if self._serper and hasattr(self._serper, "close"):
            self._serper.close()
        if self._dataforseo and hasattr(self._dataforseo, "close"):
            self._dataforseo.close()
