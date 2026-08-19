"""Shared URL validation and Ghost/RSS checking utilities.

Extracted from health_check.py for reuse across modules.
"""

import re
from urllib.parse import urlparse

import httpx
import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger()

# User agent for all requests
UA = "Mozilla/5.0 (compatible; DraftSpringBot/1.0; +https://draftspring.io)"

_PRIVATE_PATTERNS = [
    re.compile(r"^127\."),
    re.compile(r"^10\."),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^0\."),
]


def is_private_ip(host: str) -> bool:
    """Check if a hostname/IP is private or localhost."""
    if host in ("localhost", "0.0.0.0", "[::]", "[::1]"):
        return True
    for pattern in _PRIVATE_PATTERNS:
        if pattern.match(host):
            return True
    return False


def validate_url(url: str) -> str | None:
    """Validate and normalize URL. Returns cleaned URL or None.

    Normalization:
    - Strip whitespace and trailing slashes
    - Upgrade http → https
    - Strip www. prefix from hostname
    """
    if not url or not url.strip():
        return None
    url = url.strip().rstrip("/")
    # Check scheme first
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return None
    # Add scheme if missing — but only if it looks like a domain
    if not url.startswith(("http://", "https://")):
        if "." in url:
            url = "https://" + url
        else:
            return None
        try:
            parsed = urlparse(url)
        except Exception:
            return None
    if not parsed.hostname:
        return None
    if is_private_ip(parsed.hostname):
        return None
    # Normalize: always https, strip www.
    hostname = parsed.hostname
    if hostname.startswith("www."):
        hostname = hostname[4:]
    normalized = f"https://{hostname}"
    if parsed.port and parsed.port not in (80, 443):
        normalized += f":{parsed.port}"
    if parsed.path and parsed.path != "/":
        normalized += parsed.path.rstrip("/")
    return normalized


def extract_base_url(url: str) -> str:
    """Extract scheme + host (+ port) from a URL. Used for site-root operations.

    Example: https://lowcode.agency/blog/rss.xml → https://lowcode.agency
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    base = f"https://{hostname}"
    if parsed.port and parsed.port not in (80, 443):
        base += f":{parsed.port}"
    return base


def check_ghost(html: str) -> bool:
    """Check for Ghost meta generator tag."""
    if not html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "generator"})
    if meta and meta.get("content", "").lower().startswith("ghost"):
        return True
    return False


def _is_rss(text: str, content_type: str) -> bool:
    """Check if response content looks like RSS/Atom XML."""
    ct = content_type.lower()
    if any(x in ct for x in ("xml", "rss", "atom")):
        return True
    stripped = text.strip()[:500]
    return stripped.startswith("<?xml") or "<rss" in stripped or "<feed" in stripped


def _safe_url(resp: httpx.Response, fallback: str) -> str:
    """Get final URL from response, falling back if request isn't attached."""
    try:
        return str(resp.url)
    except Exception:
        return fallback


async def discover_rss_url(
    client: httpx.AsyncClient,
    base_url: str,
    original_url: str | None = None,
) -> tuple[str | None, str | None]:
    """Discover an RSS/Atom feed for a site.

    3-tier strategy:
      1. Check if original_url itself is already an RSS feed
      2. Fetch the site homepage HTML, parse <link rel="alternate"> tags
      3. Try common RSS path patterns as fallback

    Returns (rss_text, rss_url) or (None, None).
    """
    # Reuse the Tier-1 response for Tier-2 if the URL is the same as base_url
    homepage_resp = None

    # ── Tier 1: Direct RSS check ──────────────────────────────────────
    # If the user gave a URL that IS an RSS feed, just use it.
    check_url = original_url or base_url
    try:
        resp = await client.get(check_url, headers={"User-Agent": UA})
        if resp.status_code == 200:
            if _is_rss(resp.text, resp.headers.get("content-type", "")):
                found_url = _safe_url(resp, check_url)
                logger.info("rss_discovered", tier=1, url=found_url)
                return resp.text, found_url
            # If check_url == base_url and it returned HTML, save for Tier 2
            if check_url == base_url and "html" in resp.headers.get("content-type", "").lower():
                homepage_resp = resp
    except Exception:
        pass

    # ── Tier 2: HTML <link> autodiscovery ─────────────────────────────
    # The standard way blogs advertise their feeds.
    try:
        if homepage_resp is None:
            homepage_resp = await client.get(base_url, headers={"User-Agent": UA})
        if homepage_resp.status_code == 200 and "html" in homepage_resp.headers.get("content-type", "").lower():
            soup = BeautifulSoup(homepage_resp.text, "html.parser")
            for link in soup.find_all("link", attrs={"rel": "alternate"}):
                link_type = (link.get("type") or "").lower()
                if "rss" in link_type or "atom" in link_type:
                    href = link.get("href", "").strip()
                    if not href:
                        continue
                    # Resolve relative URLs
                    if href.startswith("/"):
                        href = f"{base_url}{href}"
                    elif not href.startswith("http"):
                        href = f"{base_url}/{href}"
                    try:
                        r = await client.get(href, headers={"User-Agent": UA})
                        if r.status_code == 200 and _is_rss(r.text, r.headers.get("content-type", "")):
                            found_url = _safe_url(r, href)
                            logger.info("rss_discovered", tier=2, url=found_url)
                            return r.text, found_url
                    except Exception:
                        continue
    except Exception:
        pass

    # ── Tier 3: Path guessing (fallback) ──────────────────────────────
    # Covers Ghost, WordPress, Hugo, Webflow, Docusaurus, Substack, etc.
    rss_paths = [
        "/rss/", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml",
        "/blog/rss/", "/blog/feed/", "/blog/rss.xml",
        "/index.xml",
    ]
    for path in rss_paths:
        try:
            rss_url = f"{base_url}{path}"
            resp = await client.get(rss_url, headers={"User-Agent": UA})
            if resp.status_code == 200 and _is_rss(resp.text, resp.headers.get("content-type", "")):
                found_url = _safe_url(resp, rss_url)
                logger.info("rss_discovered", tier=3, url=found_url, path=path)
                return resp.text, found_url
        except Exception:
            continue

    return None, None
