#!/usr/bin/env python3
"""
ABOUTME: OpenAIRE API client for open-access scholarly metadata search
ABOUTME: Complements Crossref/OpenAlex with repository-heavy European coverage
"""

import logging
from typing import Optional, Dict, Any

from .base import BaseAPIClient, validate_author_name

logger = logging.getLogger(__name__)


class OpenAIREClient(BaseAPIClient):
    """OpenAIRE publication search client (incremental provider integration)."""

    def __init__(self, timeout: int = 15, max_retries: int = 3):
        super().__init__(
            base_url="https://api.openaire.eu",
            rate_limit_per_second=5.0,
            timeout=timeout,
            max_retries=max_retries,
        )

    def search_paper(self, query: str) -> Optional[Dict[str, Any]]:
        response = self._make_request(
            method="GET",
            endpoint="/search/publications",
            params={
                "keywords": query,
                "format": "json",
                "size": 5,
            },
        )
        if not response:
            return None

        try:
            results = (
                response.get("response", {})
                .get("results", {})
                .get("result", [])
            )
            if not isinstance(results, list) or not results:
                return None

            best = results[0]
            metadata = best.get("metadata", {}) if isinstance(best, dict) else {}
            oaf = metadata.get("oaf:entity", {}).get("oaf:result", {}) if isinstance(metadata, dict) else {}

            title = ""
            title_node = oaf.get("title")
            if isinstance(title_node, dict):
                title = str(title_node.get("$", "") or "").strip()
            elif isinstance(title_node, str):
                title = title_node.strip()

            if not title:
                return None

            date_str = str(oaf.get("dateofacceptance", "") or "")
            year = None
            if len(date_str) >= 4 and date_str[:4].isdigit():
                year = int(date_str[:4])

            url = ""
            pid = oaf.get("pid")
            if isinstance(pid, dict):
                url = str(pid.get("$", "") or "").strip()
            elif isinstance(pid, list) and pid:
                first = pid[0]
                if isinstance(first, dict):
                    url = str(first.get("$", "") or "").strip()
                else:
                    url = str(first or "").strip()

            authors = []
            creator = oaf.get("creator")
            if isinstance(creator, dict):
                c = str(creator.get("$", "") or "").strip()
                if c:
                    last = c.split()[-1]
                    ok, _ = validate_author_name(last)
                    if ok:
                        authors = [last]
            elif isinstance(creator, list):
                for cnode in creator:
                    c = str(cnode.get("$", "") if isinstance(cnode, dict) else cnode or "").strip()
                    if not c:
                        continue
                    last = c.split()[-1]
                    ok, _ = validate_author_name(last)
                    if ok:
                        authors.append(last)
                authors = authors[:5]

            if not authors:
                authors = ["OpenAIRE"]

            return {
                "title": title,
                "authors": authors,
                "year": year,
                "doi": "",
                "url": url,
                "journal": "",
                "publisher": "OpenAIRE",
                "source_type": "journal",
                "confidence": 0.72,
            }
        except Exception as e:
            logger.debug(f"OpenAIRE parse error: {e}")
            return None
