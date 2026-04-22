#!/usr/bin/env python3
"""
ABOUTME: DOAJ API client for directory of open access journals
ABOUTME: Optional provider to increase OA article coverage in citation research
"""

import logging
from typing import Optional, Dict, Any
from urllib.parse import quote

from .base import BaseAPIClient, validate_author_name

logger = logging.getLogger(__name__)


class DOAJClient(BaseAPIClient):
    """DOAJ article search client."""

    def __init__(self, timeout: int = 15, max_retries: int = 3):
        super().__init__(
            base_url="https://doaj.org/api",
            rate_limit_per_second=5.0,
            timeout=timeout,
            max_retries=max_retries,
        )

    def search_paper(self, query: str) -> Optional[Dict[str, Any]]:
        response = self._make_request(
            method="GET",
            endpoint=f"/search/articles/{quote(query, safe='')}",
            params={"pageSize": 5},
        )
        if not response:
            return None

        try:
            results = response.get("results", [])
            if not isinstance(results, list) or not results:
                return None

            bib = (results[0] or {}).get("bibjson", {})
            title = str(bib.get("title", "") or "").strip()
            if not title:
                return None

            year = None
            year_str = str(bib.get("year", "") or "").strip()
            if year_str.isdigit() and len(year_str) == 4:
                year = int(year_str)

            doi = ""
            for ident in bib.get("identifier", []) or []:
                if str(ident.get("type", "")).lower() == "doi":
                    doi = str(ident.get("id", "") or "").strip()
                    break

            url = ""
            for link in bib.get("link", []) or []:
                candidate = str(link.get("url", "") or "").strip()
                if candidate:
                    url = candidate
                    break

            authors = []
            for a in (bib.get("author") or [])[:6]:
                name = str(a.get("name", "") if isinstance(a, dict) else a or "").strip()
                if not name:
                    continue
                last = name.split()[-1]
                ok, _ = validate_author_name(last)
                if ok:
                    authors.append(last)
            if not authors:
                authors = ["DOAJ"]

            journal = str((bib.get("journal") or {}).get("title", "") or "").strip()

            return {
                "title": title,
                "authors": authors,
                "year": year,
                "doi": doi,
                "url": url,
                "journal": journal,
                "publisher": "DOAJ",
                "source_type": "journal",
                "abstract": bib.get("abstract"),
                "confidence": 0.71,
            }
        except Exception as e:
            logger.debug(f"DOAJ parse error: {e}")
            return None
