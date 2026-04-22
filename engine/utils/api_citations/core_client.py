#!/usr/bin/env python3
"""
ABOUTME: CORE API client for open-access scholarly works
ABOUTME: Optional provider to expand citation coverage beyond core academic APIs
"""

import os
import logging
from typing import Optional, Dict, Any

from .base import BaseAPIClient, validate_author_name

logger = logging.getLogger(__name__)


class COREClient(BaseAPIClient):
    """CORE API client (https://core.ac.uk/)."""

    def __init__(self, timeout: int = 15, max_retries: int = 3):
        self.core_api_key = os.getenv("CORE_API_KEY", "").strip()
        super().__init__(
            base_url="https://api.core.ac.uk/v3",
            rate_limit_per_second=3.0,
            timeout=timeout,
            max_retries=max_retries,
        )
        if self.core_api_key:
            self.session.headers.update({"Authorization": f"Bearer {self.core_api_key}"})

    def search_paper(self, query: str) -> Optional[Dict[str, Any]]:
        response = self._make_request(
            method="GET",
            endpoint="/search/works",
            params={
                "q": query,
                "limit": 5,
            },
        )
        if not response:
            return None

        try:
            results = response.get("results", [])
            if not isinstance(results, list) or not results:
                return None

            paper = results[0]
            title = str(paper.get("title", "") or "").strip()
            if not title:
                return None

            year = paper.get("yearPublished") or paper.get("year")
            try:
                year = int(year) if year is not None else None
            except Exception:
                year = None

            doi = str(paper.get("doi", "") or "").strip()
            url = str(paper.get("downloadUrl") or paper.get("sourceFulltextUrls", [""])[0] or "").strip()

            authors = []
            for author in (paper.get("authors") or [])[:6]:
                name = str(author.get("name") if isinstance(author, dict) else author or "").strip()
                if not name:
                    continue
                last = name.split()[-1]
                ok, _ = validate_author_name(last)
                if ok:
                    authors.append(last)
            if not authors:
                authors = ["CORE"]

            journal = str(paper.get("journal") or "").strip()
            publisher = str(paper.get("publisher") or "CORE").strip()

            return {
                "title": title,
                "authors": authors,
                "year": year,
                "doi": doi,
                "url": url,
                "journal": journal,
                "publisher": publisher,
                "source_type": "journal",
                "abstract": paper.get("abstract"),
                "confidence": 0.73,
            }
        except Exception as e:
            logger.debug(f"CORE parse error: {e}")
            return None
