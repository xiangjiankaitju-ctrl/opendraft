#!/usr/bin/env python3
"""
ABOUTME: Base API client with error handling, retries, and rate limiting
ABOUTME: Provides production-grade HTTP request infrastructure for academic APIs
"""

import time
import logging
import random
import datetime
import requests
from typing import Optional, Dict, Any, List

# Backpressure integration for cross-container rate limit coordination
_backpressure_manager = None
def get_backpressure_manager():
    """Lazy-load backpressure manager to avoid circular imports."""
    global _backpressure_manager
    if _backpressure_manager is None:
        try:
            from utils.backpressure import BackpressureManager
            _backpressure_manager = BackpressureManager()
        except ImportError:
            pass
    return _backpressure_manager
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


# =========================================================================
# Unified citation API response normalization
# =========================================================================

_SOURCE_TYPE_NORMALIZATION = {
    "academic": "journal",
    "journal": "journal",
    "conference": "conference",
    "proceedings": "conference",
    "book": "book",
    "report": "report",
    "industry": "report",
    "website": "website",
    "article": "website",
    "news": "website",
    "preprint": "preprint",
}


def _pick_first_nonempty(*values: Any) -> str:
    """Return first meaningful string-like value from heterogeneous metadata."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _pick_first_nonempty(
                value.get("display_name"),
                value.get("name"),
                value.get("title"),
                value.get("publisher"),
            )
            if nested:
                return nested
        if isinstance(value, list):
            for item in value:
                nested = _pick_first_nonempty(item)
                if nested:
                    return nested
    return ""


def normalize_citation_metadata(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Normalize heterogeneous API responses to a unified citation schema.

    Unified fields:
    - title, authors(list[str]), year(int), doi, url
    - journal, publisher, volume, issue, pages
    - source_type, abstract, citation_count
    """
    if not metadata or not isinstance(metadata, dict):
        return None

    title = str(metadata.get("title", "")).strip()
    if not title:
        return None

    # Normalize authors to list[str]
    authors_raw = metadata.get("authors")
    authors: List[str] = []
    if isinstance(authors_raw, list):
        authors = [str(a).strip() for a in authors_raw if str(a).strip()]
    elif isinstance(authors_raw, str) and authors_raw.strip():
        # If passed as "X et al." or comma-separated string, keep first segment
        first = authors_raw.split(",")[0].strip()
        if first:
            authors = [first]

    # Normalize year
    year = metadata.get("year")
    if isinstance(year, str):
        year_digits = "".join(ch for ch in year if ch.isdigit())
        year = int(year_digits[:4]) if len(year_digits) >= 4 else None
    elif isinstance(year, float):
        year = int(year)
    elif not isinstance(year, int):
        year = None

    current_year = datetime.datetime.now().year
    if year is not None and (year < 1900 or year > current_year + 2):
        year = None

    source_type_raw = str(metadata.get("source_type", "website")).lower().strip()
    source_type = _SOURCE_TYPE_NORMALIZATION.get(source_type_raw, "website")

    journal = _pick_first_nonempty(
        metadata.get("journal"),
        metadata.get("journal_name"),
        metadata.get("venue"),
        metadata.get("container_title"),
        metadata.get("container-title"),
        metadata.get("publication"),
        metadata.get("source"),
        metadata.get("host_venue"),
        metadata.get("primary_location"),
        metadata.get("booktitle"),
    )
    publisher = _pick_first_nonempty(
        metadata.get("publisher"),
        metadata.get("institution"),
        metadata.get("organization"),
        metadata.get("host_venue"),
        metadata.get("primary_location"),
    )

    normalized = {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": str(metadata.get("doi", "") or "").strip(),
        "url": str(metadata.get("url", "") or "").strip(),
        "journal": journal,
        "publisher": publisher,
        "volume": str(metadata.get("volume", "") or "").strip(),
        "issue": str(metadata.get("issue", "") or "").strip(),
        "pages": str(metadata.get("pages", "") or "").strip(),
        "source_type": source_type,
        "abstract": metadata.get("abstract") or metadata.get("snippet"),
        "citation_count": metadata.get("citation_count"),
    }

    # Drop empty optional text fields for cleaner downstream checks
    for key in ["doi", "url", "journal", "publisher", "volume", "issue", "pages"]:
        if not normalized[key]:
            normalized[key] = ""

    return normalized

# Browser User-Agent pool for rotation (reduces rate limiting)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Optional proxy configuration loaded from environment
# Format: comma-separated "host:port:username:password" strings
# Example: PROXY_LIST_ENV="proxy1.com:8080:user:pass,proxy2.com:8080:user:pass"
import os

def _load_proxy_list() -> list:
    """Load and validate proxy list from PROXY_LIST environment variable."""
    proxy_env = os.getenv('PROXY_LIST', '')
    if not proxy_env:
        return []

    proxies = [p.strip() for p in proxy_env.split(',') if p.strip()]

    # Log proxy count (don't log credentials for security)
    # Use DEBUG level to avoid showing in CLI mode
    if proxies:
        logger.debug(f"Loaded {len(proxies)} proxies for rotation")
        # Validate format (basic check)
        for idx, proxy in enumerate(proxies, 1):
            parts = proxy.split(':')
            if len(parts) not in [2, 4]:
                logger.warning(f"Proxy {idx} has unexpected format (expected host:port or host:port:user:pass)")

    return proxies

PROXY_LIST: list = _load_proxy_list()

def mask_credentials(url: str) -> str:
    """Mask credentials in URL for safe logging."""
    import re
    # Pattern: user:password@ in URLs
    return re.sub(r'://([^:]+):([^@]+)@', r'://\1:****@', url)

def parse_proxy(proxy_str: str) -> dict:
    """Parse proxy string to requests-compatible dict.

    Security note: Credentials are embedded in URL but never logged.
    Use mask_credentials() for any logging of proxy URLs.
    """
    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        proxy_url = f"http://{user}:{password}@{host}:{port}"
    elif len(parts) == 2:
        host, port = parts
        proxy_url = f"http://{host}:{port}"
    else:
        return {}
    return {"http": proxy_url, "https": proxy_url}

# =========================================================================
# SSRF Protection - Validate URLs before making requests
# =========================================================================

import ipaddress
from urllib.parse import urlparse

def is_safe_url(url: str) -> tuple:
    """
    Validate URL is safe from SSRF attacks.

    Args:
        url: URL to validate

    Returns:
        Tuple of (is_safe, reason)
    """
    if not url:
        return (False, "empty_url")

    try:
        parsed = urlparse(url)
    except Exception:
        return (False, "invalid_url")

    # Only allow http/https schemes
    if parsed.scheme not in ('http', 'https'):
        return (False, f"unsafe_scheme_{parsed.scheme}")

    # Block internal/private IP ranges
    hostname = parsed.hostname
    if hostname:
        # Check for localhost variations
        if hostname in ('localhost', '127.0.0.1', '::1', '0.0.0.0'):
            return (False, "localhost_blocked")

        # Try to parse as IP address to check for private ranges
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return (False, "private_ip_blocked")
        except ValueError:
            # Not an IP address, hostname is fine
            pass

        # Block cloud metadata endpoints
        metadata_hosts = ['169.254.169.254', 'metadata.google.internal', 'metadata.azure.com']
        if hostname in metadata_hosts:
            return (False, "cloud_metadata_blocked")

    return (True, "valid")

# Standard browser headers
BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# =========================================================================
# Citation Quality Validation Functions (Fix 2 & Fix 5)
# =========================================================================

import datetime

CURRENT_YEAR = datetime.datetime.now().year

def validate_author_name(author_name: str) -> tuple:
    """
    Validate author name is academically acceptable.
    
    Args:
        author_name: Author name string
        
    Returns:
        Tuple of (is_valid, reason)
    """
    if not author_name:
        return (False, "empty")
    
    name = author_name.strip()
    
    # Reject single-character authors (e.g., "R et al.")
    if len(name) <= 2:
        return (False, "too_short")
    
    # Reject domain-like authors (e.g., "education.illinois.edu")
    domain_tlds = ['.com', '.org', '.net', '.edu', '.gov', '.io', '.ai', '.int']
    if '.' in name and any(tld in name.lower() for tld in domain_tlds):
        return (False, "domain_as_author")
    
    # Reject URLs as authors
    if name.startswith('http://') or name.startswith('https://'):
        return (False, "url_as_author")
    
    # Reject generic/institutional author names (metadata pollution)
    generic_terms = [
        'working paper', 'discussion paper', 'technical report', 'staff report',
        'research paper', 'policy brief', 'white paper', 'occasional paper',
        'series', 'anonymous', 'unknown', 'author', 'authors', 'editor', 'editors',
        'committee', 'commission', 'group', 'team', 'staff', 'admin', 'administrator'
    ]
    name_lower = name.lower()
    if any(term in name_lower for term in generic_terms):
        return (False, "generic_author")
    
    return (True, "valid")

def validate_publication_year(year: int) -> tuple:
    """
    Validate publication year is reasonable.
    
    Args:
        year: Publication year
        
    Returns:
        Tuple of (is_valid, reason, is_recent)
    """
    if not year:
        return (False, "no_year", False)
    
    try:
        year_int = int(year)
    except (ValueError, TypeError):
        return (False, "invalid_year", False)
    
    # Future years are impossible
    if year_int > CURRENT_YEAR:
        return (False, "future_year", False)
    
    # Very old papers (pre-1900) are suspicious
    if year_int < 1900:
        return (False, "ancient_year", False)
    
    # Current year papers might be preprints
    is_recent = (year_int == CURRENT_YEAR)
    
    return (True, "valid", is_recent)


class BaseAPIClient(ABC):
    """
    Base class for academic API clients.

    Provides:
    - Exponential backoff retries
    - Rate limiting
    - Error handling
    - Request logging
    - Client IP forwarding for distributed rate limits
    """

    # Thread-local storage for client IP (set per-request context)
    _client_ip_context: Optional[str] = None

    @classmethod
    def set_client_ip(cls, client_ip: Optional[str]) -> None:
        """Set client IP for rate limit distribution (called per thesis context)."""
        cls._client_ip_context = client_ip

    @classmethod
    def get_client_ip(cls) -> Optional[str]:
        """Get current client IP context."""
        return cls._client_ip_context

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        rate_limit_per_second: float = 10.0,
        timeout: int = 10,
        max_retries: int = 3,
        api_type: Optional[str] = None,
    ):
        """
        Initialize API client.

        Args:
            base_url: Base URL for API
            api_key: Optional API key for authenticated requests
            rate_limit_per_second: Maximum requests per second
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts for failed requests
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.rate_limit_per_second = rate_limit_per_second
        self.timeout = timeout
        self.max_retries = max_retries
        self.api_type = api_type  # For backpressure signaling
        self.deadline_ts: Optional[float] = None

        # Rate limiting state
        self.last_request_time: float = 0.0
        self.min_interval: float = 1.0 / rate_limit_per_second

        # Session for connection pooling
        self.session = requests.Session()
        # Apply browser headers (User-Agent rotated per request)
        self.session.headers.update(BROWSER_HEADERS)

    def set_deadline(self, deadline_ts: Optional[float]) -> None:
        """Set optional monotonic-time deadline for subsequent HTTP requests."""
        self.deadline_ts = deadline_ts

    def _time_remaining(self) -> Optional[float]:
        if self.deadline_ts is None:
            return None
        return max(0.0, self.deadline_ts - time.monotonic())

    def _request_timeout(self) -> float:
        """Return a per-request timeout capped by remaining deadline budget."""
        remaining = self._time_remaining()
        if remaining is None:
            return float(self.timeout)
        # Keep a small reserve for parsing/fallback bookkeeping. If the caller is
        # too close to deadline, fail fast rather than starting a long request.
        return max(0.25, min(float(self.timeout), remaining - 0.5))

    def _rate_limit_wait(self) -> None:
        """Wait if necessary to respect rate limit."""
        current_time = time.time()
        time_since_last_request = current_time - self.last_request_time

        if time_since_last_request < self.min_interval:
            sleep_time = self.min_interval - time_since_last_request
            logger.debug(f"Rate limit: sleeping {sleep_time:.3f}s")
            time.sleep(sleep_time)

        self.last_request_time = time.time()

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Make HTTP request with retries and error handling.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (relative to base_url)
            params: Query parameters
            json_data: JSON request body

        Returns:
            Response JSON dict or None if all retries failed
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        for attempt in range(self.max_retries):
            try:
                remaining = self._time_remaining()
                if remaining is not None and remaining <= 0.75:
                    logger.debug(f"Skipping request: deadline exhausted for {url[:60]}...")
                    return None
                # Rate limiting
                self._rate_limit_wait()

                # Make request
                logger.debug(f"Request: {method} {url} (attempt {attempt + 1}/{self.max_retries})")

                # Rotate User-Agent for each request to avoid rate limiting
                headers = {"User-Agent": random.choice(USER_AGENTS)}

                # Forward client IP for rate limit distribution (helps avoid 429 across users)
                client_ip = self.get_client_ip()
                if client_ip and client_ip != 'unknown':
                    headers["X-Forwarded-For"] = client_ip

                # Add API key header if available (e.g., Semantic Scholar uses x-api-key)
                if self.api_key:
                    headers["x-api-key"] = self.api_key
                
                # Select proxy for this request
                proxy_str = random.choice(PROXY_LIST) if PROXY_LIST else None
                proxy_dict = parse_proxy(proxy_str) if proxy_str else None
                
                
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                    headers=headers,
                    timeout=self._request_timeout(),
                    proxies=proxy_dict,
                )

                # Check status code
                if response.status_code == 200:
                    bp = get_backpressure_manager()
                    if bp and self.api_type:
                        try:
                            from utils.backpressure import APIType
                            bp.signal_success(APIType(self.api_type))
                        except ValueError:
                            pass
                    return response.json()

                elif response.status_code == 404:
                    logger.debug(f"Resource not found: {url}")
                    return None  # Not found is not an error, just no result

                elif response.status_code == 429:
                    # Rate limited - with proxy rotation, retry immediately with different proxy
                    
                    bp = get_backpressure_manager()
                    proxy_used = random.choice(PROXY_LIST) if PROXY_LIST else None
                    if bp and self.api_type:
                        from utils.backpressure import APIType
                        try:
                            api_enum = APIType(self.api_type)
                            bp.signal_429(api_enum, proxy_id=proxy_used if proxy_used else None)
                        except ValueError:
                            pass  # Unknown API type

                    # Semantic Scholar is supplemental and particularly prone to
                    # rate limits. Cool it down and fail fast instead of spending
                    # Scout budget in exponential sleeps.
                    if self.api_type == "semantic_scholar":
                        logger.warning("Semantic Scholar rate limited (429); entering cooldown and skipping retries")
                        return None

                    # With proxies: minimal delay (next request uses different proxy)
                    # Without proxies: exponential backoff (Semantic Scholar needs longer waits)
                    if PROXY_LIST:
                        wait_time = 0.5  # Minimal delay, rely on proxy rotation
                    else:
                        # Exponential backoff: 3s, 6s, 12s, 24s, 48s for attempts 1-5
                        # This gives Semantic Scholar time to reset rate limits
                        wait_time = 3 * (2 ** attempt)
                    remaining = self._time_remaining()
                    if remaining is not None and remaining <= wait_time + 0.75:
                        logger.debug("Skipping 429 retry: deadline nearly exhausted")
                        return None
                    logger.debug(f"Rate limited (429), waiting {wait_time:.1f}s before retry (attempt {attempt + 1}/{self.max_retries})")
                    time.sleep(wait_time)
                    continue

                elif response.status_code >= 500:
                    bp = get_backpressure_manager()
                    if bp and self.api_type:
                        try:
                            from utils.backpressure import APIType
                            bp.signal_failure(APIType(self.api_type), reason=f"http_{response.status_code}")
                        except ValueError:
                            pass
                    # Server error - retry (with proxies: minimal delay, without: exponential backoff)
                    wait_time = 0.5 if PROXY_LIST else 2**attempt
                    remaining = self._time_remaining()
                    if remaining is not None and remaining <= wait_time + 0.75:
                        logger.debug("Skipping server-error retry: deadline nearly exhausted")
                        return None
                    logger.warning(f"Server error ({response.status_code}), waiting {wait_time}s before retry")
                    time.sleep(wait_time)
                    continue

                else:
                    # Client error - don't retry
                    bp = get_backpressure_manager()
                    if bp and self.api_type:
                        try:
                            from utils.backpressure import APIType
                            bp.signal_failure(APIType(self.api_type), reason=f"http_{response.status_code}")
                        except ValueError:
                            pass
                    logger.error(f"Client error: {response.status_code} - {response.text[:200]}")
                    return None

            except requests.exceptions.Timeout:
                bp = get_backpressure_manager()
                if bp and self.api_type:
                    try:
                        from utils.backpressure import APIType
                        bp.signal_failure(APIType(self.api_type), reason="timeout")
                    except ValueError:
                        pass
                # With proxies: minimal delay, without: exponential backoff
                wait_time = 0.5 if PROXY_LIST else 2**attempt
                remaining = self._time_remaining()
                if remaining is not None and remaining <= wait_time + 0.75:
                    logger.debug("Skipping timeout retry: deadline nearly exhausted")
                    return None
                logger.warning(f"Request timeout, waiting {wait_time}s before retry")
                time.sleep(wait_time)
                continue

            except requests.exceptions.ConnectionError as e:
                bp = get_backpressure_manager()
                if bp and self.api_type:
                    try:
                        from utils.backpressure import APIType
                        bp.signal_failure(APIType(self.api_type), reason="connection_error")
                    except ValueError:
                        pass
                # With proxies: minimal delay, without: exponential backoff
                wait_time = 0.5 if PROXY_LIST else 2**attempt
                remaining = self._time_remaining()
                if remaining is not None and remaining <= wait_time + 0.75:
                    logger.debug("Skipping connection-error retry: deadline nearly exhausted")
                    return None
                logger.warning(f"Connection error: {e}, waiting {wait_time}s before retry")
                time.sleep(wait_time)
                continue

            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")
                return None

            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                return None

        # All retries exhausted - this is normal, other citation sources will be tried
        # Using debug level since fallback chains handle this gracefully
        logger.debug(f"API unavailable after {self.max_retries} retries: {url[:60]}... (fallback sources will be used)")
        return None

    @abstractmethod
    def search_paper(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Search for a paper by query.

        Must be implemented by subclasses.

        Args:
            query: Search query (title, authors, keywords)

        Returns:
            Paper metadata dict or None if not found
        """
        pass

    def close(self) -> None:
        """Close the session."""
        self.session.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
