"""Ghost Content Health Check — public tool endpoint.

Checks a Ghost blog URL for:
- Ghost verification (meta generator tag)
- RSS activity (posts in last 30/60/90 days)
- Publishing frequency (posts/month)
- Basic SEO (title length, meta description, og:image) on homepage + post pages
- Structured data (JSON-LD / schema.org)
- Sitemap presence and post count
- Internal linking between posts
- Image alt text coverage
- Post content length (word count)
- Categories/tags usage

Scoring 0-100 with recommendations.
"""

import html
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlparse, urljoin

import httpx
import structlog
from bs4 import BeautifulSoup
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from app.utils.url_validation import discover_rss_url as _shared_discover_rss, extract_base_url

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])

# User agent for all requests
_UA = "Mozilla/5.0 (compatible; DraftSpringBot/1.0; +https://draftspring.io)"

# ---------------------------------------------------------------------------
# Rate limiter (in-memory, per-IP, 10 req/min)
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.time()

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        # Periodic cleanup every 5 minutes
        if now - self._last_cleanup > 300:
            self._cleanup(now)
            self._last_cleanup = now

        window_start = now - self.window_seconds
        self._requests[ip] = [t for t in self._requests[ip] if t > window_start]
        if len(self._requests[ip]) >= self.max_requests:
            return False
        self._requests[ip].append(now)
        return True

    def _cleanup(self, now: float):
        cutoff = now - self.window_seconds
        dead_keys = [ip for ip, times in self._requests.items() if not times or times[-1] < cutoff]
        for k in dead_keys:
            del self._requests[k]

    def clear(self):
        self._requests.clear()


_rate_limiter = RateLimiter()

# ---------------------------------------------------------------------------
# URL validation & private IP detection
# ---------------------------------------------------------------------------

_PRIVATE_PATTERNS = [
    re.compile(r"^127\."),
    re.compile(r"^10\."),
    re.compile(r"^172\.(1[6-9]|2[0-9]|3[01])\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^0\."),
]


def _is_private_ip(host: str) -> bool:
    """Check if a hostname/IP is private or localhost."""
    if host in ("localhost", "0.0.0.0", "[::]", "[::1]"):
        return True
    for pattern in _PRIVATE_PATTERNS:
        if pattern.match(host):
            return True
    return False


def _validate_url(url: str) -> str | None:
    """Validate and normalize URL. Returns cleaned URL or None."""
    if not url or not url.strip():
        return None
    url = url.strip().rstrip("/")
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not parsed.hostname:
        return None
    if _is_private_ip(parsed.hostname):
        return None
    return url


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check_ghost(html: str) -> bool:
    """Check for Ghost meta generator tag."""
    if not html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    meta = soup.find("meta", attrs={"name": "generator"})
    if meta and meta.get("content", "").lower().startswith("ghost"):
        return True
    return False


def _check_seo(html: str) -> dict:
    """Check basic SEO: title length, meta description, og:image."""
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    title_length = len(title_text)

    # Meta description
    meta_desc = soup.find("meta", attrs={"name": "description"})
    desc_content = meta_desc.get("content", "").strip() if meta_desc else ""
    has_meta_description = bool(desc_content)

    # OG image
    og_image = soup.find("meta", attrs={"property": "og:image"})
    has_og_image = bool(og_image and og_image.get("content", "").strip())

    return {
        "title_length": title_length,
        "title_text": title_text,
        "has_meta_description": has_meta_description,
        "has_og_image": has_og_image,
    }


def _check_structured_data(html: str) -> bool:
    """Check for JSON-LD or schema.org markup."""
    soup = BeautifulSoup(html, "html.parser")
    # JSON-LD
    if soup.find("script", attrs={"type": "application/ld+json"}):
        return True
    # schema.org itemtype
    if soup.find(attrs={"itemtype": re.compile(r"schema\.org", re.I)}):
        return True
    return False


def _check_post_page(html: str, base_url: str) -> dict:
    """Analyze a single post page for content quality signals."""
    soup = BeautifulSoup(html, "html.parser")

    # SEO checks
    seo = _check_seo(html)
    has_structured_data = _check_structured_data(html)

    # Word count — strip nav, footer, scripts, then count words in body text
    for tag in soup.find_all(["nav", "footer", "script", "style", "header"]):
        tag.decompose()
    body = soup.find("body")
    text = body.get_text(separator=" ", strip=True) if body else ""
    word_count = len(text.split())

    # Image alt text
    images = soup.find_all("img")
    total_images = 0
    images_with_alt = 0
    for img in images:
        src = img.get("src", "")
        # Skip tracking pixels and tiny images
        if not src or "pixel" in src.lower() or "tracking" in src.lower():
            continue
        total_images += 1
        alt = img.get("alt", "").strip()
        if alt:
            images_with_alt += 1

    # Internal links — links pointing to the same domain
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.hostname or ""
    internal_links = 0
    all_links = soup.find_all("a", href=True)
    for a in all_links:
        href = a["href"]
        try:
            parsed_href = urlparse(urljoin(base_url, href))
            if parsed_href.hostname and parsed_href.hostname == base_domain:
                # Skip anchors, nav links, etc — only count links to other pages
                if parsed_href.path and parsed_href.path != "/" and parsed_href.path != urlparse(base_url).path:
                    internal_links += 1
        except Exception:
            continue

    return {
        "seo": seo,
        "has_structured_data": has_structured_data,
        "word_count": word_count,
        "total_images": total_images,
        "images_with_alt": images_with_alt,
        "internal_links": internal_links,
    }


def _parse_rss_activity(rss_xml: str) -> dict:
    """Parse RSS XML and count posts by recency. Also extracts post URLs."""
    result = {
        "posts_last_30d": 0,
        "posts_last_60d": 0,
        "posts_last_90d": 0,
        "posts_per_month": 0.0,
        "total_posts": 0,
        "post_urls": [],
    }
    try:
        root = ET.fromstring(rss_xml)
    except ET.ParseError:
        return result

    now = datetime.now(timezone.utc)
    dates = []
    post_urls = []
    for item in root.iter("item"):
        pub_date = item.findtext("pubDate")
        link = item.findtext("link")
        if pub_date:
            try:
                dt = parsedate_to_datetime(pub_date)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dates.append(dt)
                if link:
                    post_urls.append(link.strip())
            except Exception:
                # Still try to capture the link even if date fails
                if link:
                    post_urls.append(link.strip())
                continue

    result["total_posts"] = len(dates)
    result["post_urls"] = post_urls
    for dt in dates:
        age = (now - dt).days
        if age <= 30:
            result["posts_last_30d"] += 1
        if age <= 60:
            result["posts_last_60d"] += 1
        if age <= 90:
            result["posts_last_90d"] += 1

    if dates:
        oldest = min(dates)
        months_span = max((now - oldest).days / 30.0, 1.0)
        result["posts_per_month"] = round(len(dates) / months_span, 1)

    return result


def _parse_sitemap(sitemap_xml: str) -> dict:
    """Parse a sitemap XML (posts sitemap or index) for post URLs and dates."""
    result = {
        "post_urls": [],
        "total_posts": 0,
        "latest_post_date": None,
        "has_sitemap": True,
    }
    try:
        root = ET.fromstring(sitemap_xml)
    except ET.ParseError:
        result["has_sitemap"] = False
        return result

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

    urls = root.findall(".//sm:url", ns)
    dates = []
    for url_elem in urls:
        loc = url_elem.findtext("sm:loc", namespaces=ns)
        lastmod = url_elem.findtext("sm:lastmod", namespaces=ns)
        if loc:
            result["post_urls"].append(loc.strip())
        if lastmod:
            try:
                # ISO format dates from sitemaps
                dt = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
                dates.append(dt)
            except Exception:
                pass

    result["total_posts"] = len(result["post_urls"])
    if dates:
        result["latest_post_date"] = max(dates)

    return result


def _parse_sitemap_index(sitemap_xml: str) -> list[str]:
    """Parse sitemap index to find sub-sitemaps (especially posts sitemap)."""
    try:
        root = ET.fromstring(sitemap_xml)
    except ET.ParseError:
        return []

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = []
    for sitemap_elem in root.findall(".//sm:sitemap", ns):
        loc = sitemap_elem.findtext("sm:loc", namespaces=ns)
        if loc:
            urls.append(loc.strip())
    return urls


async def _discover_rss_url(client: httpx.AsyncClient, base_url: str) -> tuple[str | None, str | None]:
    """Delegate to shared 3-tier RSS discovery."""
    site_root = extract_base_url(base_url)
    return await _shared_discover_rss(client, site_root, original_url=base_url)


async def _fetch_sitemap_data(client: httpx.AsyncClient, base_url: str) -> dict:
    """Fetch and parse sitemap data. Tries sitemap index → posts sitemap."""
    sitemap_data = {
        "has_sitemap": False,
        "post_urls": [],
        "total_posts": 0,
        "latest_post_date": None,
    }

    # Try sitemap index first
    try:
        resp = await client.get(f"{base_url}/sitemap.xml", headers={"User-Agent": _UA})
        if resp.status_code == 200:
            sitemap_data["has_sitemap"] = True
            sub_sitemaps = _parse_sitemap_index(resp.text)

            # Look for posts sitemap
            posts_sitemap_url = None
            for sm_url in sub_sitemaps:
                if "posts" in sm_url.lower():
                    posts_sitemap_url = sm_url
                    break

            if posts_sitemap_url:
                try:
                    posts_resp = await client.get(posts_sitemap_url, headers={"User-Agent": _UA})
                    if posts_resp.status_code == 200:
                        parsed = _parse_sitemap(posts_resp.text)
                        sitemap_data["post_urls"] = parsed["post_urls"]
                        sitemap_data["total_posts"] = parsed["total_posts"]
                        sitemap_data["latest_post_date"] = parsed["latest_post_date"]
                except Exception:
                    pass
    except Exception:
        pass

    # Fallback: try direct posts sitemap
    if not sitemap_data["post_urls"]:
        try:
            resp = await client.get(f"{base_url}/sitemap-posts.xml", headers={"User-Agent": _UA})
            if resp.status_code == 200:
                sitemap_data["has_sitemap"] = True
                parsed = _parse_sitemap(resp.text)
                sitemap_data["post_urls"] = parsed["post_urls"]
                sitemap_data["total_posts"] = parsed["total_posts"]
                sitemap_data["latest_post_date"] = parsed["latest_post_date"]
        except Exception:
            pass

    return sitemap_data


async def _check_post_pages(
    client: httpx.AsyncClient,
    post_urls: list[str],
    base_url: str,
    max_pages: int = 5,
) -> dict:
    """Fetch and analyze up to max_pages recent post URLs."""
    results = {
        "pages_checked": 0,
        "avg_word_count": 0,
        "avg_title_length": 0,
        "meta_description_pct": 0,
        "og_image_pct": 0,
        "structured_data_pct": 0,
        "avg_internal_links": 0,
        "alt_text_pct": 0,
        "total_images_checked": 0,
        "images_with_alt": 0,
        "post_details": [],
    }

    if not post_urls:
        return results

    urls_to_check = post_urls[:max_pages]
    page_results = []

    for url in urls_to_check:
        try:
            resp = await client.get(url, headers={"User-Agent": _UA})
            if resp.status_code == 200:
                page_data = _check_post_page(resp.text, base_url)
                page_results.append(page_data)
        except Exception:
            continue

    if not page_results:
        return results

    n = len(page_results)
    results["pages_checked"] = n
    results["avg_word_count"] = round(sum(p["word_count"] for p in page_results) / n)
    results["avg_title_length"] = round(sum(p["seo"]["title_length"] for p in page_results) / n)
    results["meta_description_pct"] = round(sum(1 for p in page_results if p["seo"]["has_meta_description"]) / n * 100)
    results["og_image_pct"] = round(sum(1 for p in page_results if p["seo"]["has_og_image"]) / n * 100)
    results["structured_data_pct"] = round(sum(1 for p in page_results if p["has_structured_data"]) / n * 100)
    results["avg_internal_links"] = round(sum(p["internal_links"] for p in page_results) / n, 1)

    total_imgs = sum(p["total_images"] for p in page_results)
    total_alts = sum(p["images_with_alt"] for p in page_results)
    results["total_images_checked"] = total_imgs
    results["images_with_alt"] = total_alts
    results["alt_text_pct"] = round(total_alts / total_imgs * 100) if total_imgs > 0 else 100  # No images = not a problem

    return results


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _calculate_score(
    is_ghost: bool,
    posts_last_30d: int,
    posts_last_60d: int,
    posts_last_90d: int,
    posts_per_month: float,
    title_length: int,
    has_meta_description: bool,
    has_og_image: bool,
    has_structured_data: bool,
    # New checks (optional for backward compat)
    has_sitemap: bool = False,
    post_pages: dict | None = None,
) -> int:
    """Calculate health score 0-100."""
    score = 0

    # Ghost verified: +5 (reduced from 10 to make room for new checks)
    if is_ghost:
        score += 5

    # RSS activity: +15 (30d) / +8 (60d) / +3 (90d) — reduced from 20/10/5
    if posts_last_30d > 0:
        score += 15
    elif posts_last_60d > 0:
        score += 8
    elif posts_last_90d > 0:
        score += 3

    # Publishing frequency: 4+ = +15, 2-3 = +10, 1 = +5 — reduced from 20/15/10
    if posts_per_month >= 4:
        score += 15
    elif posts_per_month >= 2:
        score += 10
    elif posts_per_month >= 1:
        score += 5

    # Homepage title length: 50-60 optimal = +5 (reduced from 10)
    if 50 <= title_length <= 60:
        score += 5

    # Homepage meta description: +10 (reduced from 15)
    if has_meta_description:
        score += 10

    # Homepage OG image: +5 (reduced from 15)
    if has_og_image:
        score += 5

    # Structured data: +5 (reduced from 10)
    if has_structured_data:
        score += 5

    # Sitemap: +5 (new)
    if has_sitemap:
        score += 5

    # Post page checks (new, up to +35)
    if post_pages and post_pages.get("pages_checked", 0) > 0:
        # Post meta descriptions: +5
        if post_pages.get("meta_description_pct", 0) >= 80:
            score += 5
        elif post_pages.get("meta_description_pct", 0) >= 50:
            score += 3

        # Post OG images: +5
        if post_pages.get("og_image_pct", 0) >= 80:
            score += 5
        elif post_pages.get("og_image_pct", 0) >= 50:
            score += 3

        # Post word count: +5
        avg_wc = post_pages.get("avg_word_count", 0)
        if avg_wc >= 1000:
            score += 5
        elif avg_wc >= 500:
            score += 3
        elif avg_wc >= 300:
            score += 1

        # Internal linking: +5
        avg_links = post_pages.get("avg_internal_links", 0)
        if avg_links >= 3:
            score += 5
        elif avg_links >= 1:
            score += 3

        # Image alt text: +5
        alt_pct = post_pages.get("alt_text_pct", 100)
        if alt_pct >= 80:
            score += 5
        elif alt_pct >= 50:
            score += 3

        # Structured data on posts: +5
        if post_pages.get("structured_data_pct", 0) >= 80:
            score += 5
        elif post_pages.get("structured_data_pct", 0) >= 50:
            score += 3

    return min(score, 100)


def _generate_recommendations(checks: dict) -> list[str]:
    """Generate actionable recommendations for failed/warning checks."""
    recs = []
    if not checks.get("is_ghost"):
        recs.append("This doesn't appear to be a Ghost blog. DraftSpring works exclusively with Ghost — consider migrating for the best content automation experience.")

    if checks.get("posts_last_30d", 0) == 0:
        if checks.get("posts_last_90d", 0) > 0:
            recs.append("No posts in the last 30 days. Consistent publishing is key for SEO and audience retention. Aim for at least 2 posts per week.")
        else:
            recs.append("No recent publishing activity detected. A dormant blog loses search rankings quickly. Start publishing regularly to recover.")

    freq = checks.get("posts_per_month", 0)
    if 0 < freq < 4:
        recs.append(f"Publishing {freq:.1f} posts/month. For strong SEO growth, aim for 8+ posts/month. DraftSpring can automate this entirely.")
    elif freq == 0 and checks.get("rss_available"):
        recs.append("Your RSS feed shows no posts. Start publishing to build organic traffic.")

    title_len = checks.get("title_length", 0)
    if title_len > 0 and not (50 <= title_len <= 60):
        if title_len < 50:
            recs.append(f"Homepage title is only {title_len} characters. Aim for 50-60 characters to maximize search result click-through rates.")
        else:
            recs.append(f"Homepage title is {title_len} characters — search engines may truncate it. Aim for 50-60 characters.")

    if not checks.get("has_meta_description"):
        recs.append("Missing meta description on homepage. Add a compelling 150-160 character description to improve search result appearance.")

    if not checks.get("has_og_image"):
        recs.append("No Open Graph image found on homepage. Add an og:image meta tag so your content looks great when shared on social media.")

    if not checks.get("has_structured_data"):
        recs.append("No structured data (JSON-LD/schema.org) detected on homepage. Adding Article schema helps search engines understand and feature your content.")

    if not checks.get("has_sitemap"):
        recs.append("No sitemap found. Ghost generates sitemaps automatically — make sure your site is accessible and not blocking crawlers.")

    # Post-level recommendations
    post_pages = checks.get("post_pages")
    if post_pages and post_pages.get("pages_checked", 0) > 0:
        meta_pct = post_pages.get("meta_description_pct", 0)
        if meta_pct < 80:
            recs.append(f"Only {meta_pct}% of your recent posts have meta descriptions. Add unique descriptions to every post for better search appearance.")

        og_pct = post_pages.get("og_image_pct", 0)
        if og_pct < 80:
            recs.append(f"Only {og_pct}% of your recent posts have Open Graph images. Add feature images to every post for better social media previews.")

        avg_wc = post_pages.get("avg_word_count", 0)
        if avg_wc < 500:
            recs.append(f"Average post length is {avg_wc} words. Longer, more comprehensive posts (1000+ words) tend to rank better in search results.")
        elif avg_wc < 1000:
            recs.append(f"Average post length is {avg_wc} words. Consider writing more in-depth content (1000+ words) for competitive keywords.")

        avg_links = post_pages.get("avg_internal_links", 0)
        if avg_links < 1:
            recs.append("Your posts have very few internal links. Link between related posts to help readers discover more content and improve SEO.")
        elif avg_links < 3:
            recs.append(f"Posts average {avg_links:.0f} internal links. Aim for 3+ internal links per post to strengthen your site structure.")

        alt_pct = post_pages.get("alt_text_pct", 100)
        if alt_pct < 80:
            recs.append(f"Only {alt_pct}% of images have alt text. Add descriptive alt text to all images for accessibility and image SEO.")

    return recs


# ---------------------------------------------------------------------------
# PostHog tracking (optional)
# ---------------------------------------------------------------------------

def _track_posthog(url_domain: str, score: int, is_ghost: bool):
    """Fire server-side PostHog event (best-effort, never blocks)."""
    try:
        from posthog import Posthog
        api_key = os.environ.get("POSTHOG_API_KEY") or os.environ.get("VITE_POSTHOG_KEY", "")
        if not api_key:
            return
        ph = Posthog(api_key, host="https://us.i.posthog.com")
        ph.capture(
            distinct_id=f"health-check:{url_domain}",
            event="health_check_completed",
            properties={
                "url_domain": url_domain,
                "score": score,
                "is_ghost": is_ghost,
            },
        )
        ph.flush()
    except Exception as e:
        logger.warning("posthog_track_failed", error=str(e))


# ---------------------------------------------------------------------------
# Mailchimp subscribe proxy
# ---------------------------------------------------------------------------

class SubscribeRequest(BaseModel):
    email: EmailStr
    url_domain: str = ""
    score: int = 0


def _status_for_check(passed: bool, warning: bool = False) -> str:
    if passed:
        return "pass"
    if warning:
        return "warning"
    return "fail"


def _status_badge(status: str) -> tuple[str, str, str]:
    if status == "pass":
        return "PASS", "#166534", "#DCFCE7"
    if status == "warning":
        return "WARNING", "#92400E", "#FEF3C7"
    return "FAIL", "#991B1B", "#FEE2E2"


def _add_utm(url: str, source: str = "health-check", medium: str = "email", campaign: str = "health-report") -> str:
    parsed = urlparse(url)
    return parsed._replace(query=urlencode({
        "utm_source": source,
        "utm_medium": medium,
        "utm_campaign": campaign,
    })).geturl()


def _build_site_findings(checks: dict) -> list[dict]:
    posts_last_30d = checks.get("posts_last_30d", 0)
    posts_last_90d = checks.get("posts_last_90d", 0)
    posts_per_month = checks.get("posts_per_month", 0)
    title_length = checks.get("title_length", 0)
    total_posts = checks.get("total_posts", 0)
    sitemap_post_count = checks.get("sitemap_post_count", 0)

    return [
        {
            "label": "Ghost Platform",
            "status": _status_for_check(checks.get("is_ghost", False)),
            "detail": "Running on Ghost CMS" if checks.get("is_ghost") else "Not detected as a Ghost blog",
        },
        {
            "label": "Recent Publishing Activity",
            "status": _status_for_check(posts_last_30d > 0, posts_last_90d > 0),
            "detail": (
                f"{posts_last_30d} post(s) published in the last 30 days"
                if posts_last_30d > 0
                else (f"No posts in 30 days, but {posts_last_90d} in the last 90 days" if posts_last_90d > 0 else "No recent posts detected")
            ),
        },
        {
            "label": "Publishing Frequency",
            "status": _status_for_check(posts_per_month >= 4, posts_per_month >= 1),
            "detail": f"{posts_per_month} posts/month average" if posts_per_month > 0 else "No publishing frequency data found",
        },
        {
            "label": "Content Inventory",
            "status": _status_for_check(total_posts >= 20, total_posts >= 5),
            "detail": f"{total_posts} total post(s) found" + (f" ({sitemap_post_count} from sitemap)" if sitemap_post_count else ""),
        },
        {
            "label": "Sitemap",
            "status": _status_for_check(checks.get("has_sitemap", False)),
            "detail": "Sitemap detected" if checks.get("has_sitemap") else "No sitemap detected",
        },
        {
            "label": "Homepage Title",
            "status": _status_for_check(50 <= title_length <= 60, title_length > 0),
            "detail": f"{title_length} characters" if title_length > 0 else "No title found",
        },
        {
            "label": "Homepage Meta Description",
            "status": _status_for_check(checks.get("has_meta_description", False)),
            "detail": "Present" if checks.get("has_meta_description") else "Missing",
        },
        {
            "label": "Open Graph Image",
            "status": _status_for_check(checks.get("has_og_image", False)),
            "detail": "Present" if checks.get("has_og_image") else "Missing",
        },
        {
            "label": "Structured Data",
            "status": _status_for_check(checks.get("has_structured_data", False)),
            "detail": "JSON-LD or schema.org detected" if checks.get("has_structured_data") else "Not detected",
        },
    ]


def _build_post_findings(post_pages: dict) -> list[dict]:
    if not post_pages or post_pages.get("pages_checked", 0) == 0:
        return []

    return [
        {
            "label": "Post Meta Descriptions",
            "status": _status_for_check(post_pages.get("meta_description_pct", 0) >= 80, post_pages.get("meta_description_pct", 0) >= 50),
            "detail": f"{post_pages.get('meta_description_pct', 0)}% of recent posts have meta descriptions",
        },
        {
            "label": "Post OG Images",
            "status": _status_for_check(post_pages.get("og_image_pct", 0) >= 80, post_pages.get("og_image_pct", 0) >= 50),
            "detail": f"{post_pages.get('og_image_pct', 0)}% of recent posts have Open Graph images",
        },
        {
            "label": "Post Length",
            "status": _status_for_check(post_pages.get("avg_word_count", 0) >= 1000, post_pages.get("avg_word_count", 0) >= 500),
            "detail": f"Average {post_pages.get('avg_word_count', 0)} words per post",
        },
        {
            "label": "Internal Linking",
            "status": _status_for_check(post_pages.get("avg_internal_links", 0) >= 3, post_pages.get("avg_internal_links", 0) >= 1),
            "detail": f"Average {post_pages.get('avg_internal_links', 0)} internal links per post",
        },
        {
            "label": "Image Alt Text",
            "status": _status_for_check(post_pages.get("alt_text_pct", 100) >= 80, post_pages.get("alt_text_pct", 100) >= 50),
            "detail": (
                f"{post_pages.get('alt_text_pct', 0)}% alt coverage ({post_pages.get('images_with_alt', 0)}/{post_pages.get('total_images_checked', 0)} images)"
                if post_pages.get("total_images_checked", 0) > 0
                else "No content images found"
            ),
        },
        {
            "label": "Post Structured Data",
            "status": _status_for_check(post_pages.get("structured_data_pct", 0) >= 80, post_pages.get("structured_data_pct", 0) >= 50),
            "detail": f"{post_pages.get('structured_data_pct', 0)}% of recent posts include structured data",
        },
    ]


def _render_findings_table(items: list[dict]) -> str:
    rows = []
    for item in items:
        label, text_color, bg_color = _status_badge(item["status"])
        rows.append(
            f"""
        <tr>
          <td style=\"padding:14px 0;border-bottom:1px solid #E5E7EB;vertical-align:top;\">
            <div style=\"font-size:15px;font-weight:600;color:#111827;\">{html.escape(item['label'])}</div>
            <div style=\"font-size:13px;line-height:1.55;color:#6B7280;margin-top:4px;\">{html.escape(item['detail'])}</div>
          </td>
          <td style=\"padding:14px 0 14px 16px;border-bottom:1px solid #E5E7EB;vertical-align:top;text-align:right;white-space:nowrap;\">
            <span style=\"display:inline-block;padding:6px 10px;border-radius:999px;background:{bg_color};color:{text_color};font-size:12px;font-weight:700;letter-spacing:0.02em;\">{label}</span>
          </td>
        </tr>"""
        )
    return "".join(rows)


def _render_health_check_report_email(result: dict) -> str:
    score = result["score"]
    checks = result["checks"]
    post_pages = checks.get("post_pages", {})
    domain = result.get("url_domain") or urlparse(result.get("url", "")).hostname or "your blog"
    site_findings = _build_site_findings(checks)
    post_findings = _build_post_findings(post_pages)
    primary_cta = _add_utm("https://app.draftspring.io/login")
    secondary_cta = _add_utm("https://app.draftspring.io/tools/try-draftspring", campaign="try-draftspring")
    tool_link = _add_utm("https://app.draftspring.io/tools/ghost-health-check", campaign="health-check-tool")
    score_color = "#10B981" if score >= 80 else "#F59E0B" if score >= 50 else "#EF4444"
    score_summary = (
        "Great shape. A few tweaks could make this blog even harder to beat."
        if score >= 80
        else "Solid base, but there are obvious gains still sitting on the table."
        if score >= 50
        else "This blog needs work. The good news: most of the fixes are straightforward."
    )
    recommendations = result.get("recommendations", [])[:6]
    recommendations_html = "".join(
        f'<li style="margin:0 0 10px 0;">{html.escape(rec)}</li>' for rec in recommendations
    )
    post_section = ""
    if post_findings:
        post_section = f"""
    <tr><td style=\"padding:0 32px 0 32px;\">
      <h2 style=\"margin:0 0 14px 0;font-size:18px;line-height:1.3;color:#111827;\">Per-post analysis</h2>
      <p style=\"margin:0 0 18px 0;font-size:14px;line-height:1.6;color:#6B7280;\">Checked {post_pages.get('pages_checked', 0)} recent post(s).</p>
      <table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" width=\"100%\">{_render_findings_table(post_findings)}</table>
    </td></tr>
    <tr><td style=\"padding:24px 32px 0 32px;\"><hr style=\"border:none;border-top:1px solid #E5E7EB;margin:0;\"></td></tr>"""

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
<title>Your Ghost Health Check report</title>
</head>
<body style=\"margin:0;padding:0;background-color:#F9FAFB;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;\">
<div style=\"display:none;max-height:0;overflow:hidden;\">Your Ghost Health Check report for {html.escape(domain)} is ready.</div>
<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" width=\"100%\" style=\"background-color:#F9FAFB;\"><tr><td align=\"center\" style=\"padding:40px 16px;\">
<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" width=\"600\" style=\"max-width:600px;width:100%;background:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;overflow:hidden;\">
<tr><td style=\"padding:32px 32px 12px 32px;\"><div style=\"font-size:24px;font-weight:800;line-height:1.1;background:linear-gradient(90deg,#60A5FA 0%,#A78BFA 50%,#22D3EE 100%);-webkit-background-clip:text;background-clip:text;color:transparent;\">DraftSpring</div></td></tr>
<tr><td style=\"padding:0 32px 28px 32px;\"><h1 style=\"margin:0 0 10px 0;font-size:30px;line-height:1.15;color:#111827;\">Your Ghost Health Check report</h1><p style=\"margin:0;font-size:15px;line-height:1.7;color:#6B7280;\">Here’s the score for <strong style=\"color:#111827;\">{html.escape(domain)}</strong>. We scanned the site-level basics plus recent posts where available.</p></td></tr>
<tr><td style=\"padding:0 32px 24px 32px;\"><table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" width=\"100%\" style=\"background:#111827;border-radius:12px;\"><tr><td style=\"padding:24px;vertical-align:middle;\"><div style=\"font-size:13px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#9CA3AF;margin-bottom:8px;\">Overall score</div><div style=\"font-size:54px;font-weight:800;line-height:1;color:{score_color};\">{score}</div><div style=\"font-size:15px;line-height:1.6;color:#E5E7EB;margin-top:10px;\">{html.escape(score_summary)}</div></td></tr></table></td></tr>
<tr><td style=\"padding:0 32px 24px 32px;\"><table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" width=\"100%\"><tr><td width=\"50%\" style=\"padding-right:8px;\"><a href=\"{primary_cta}\" target=\"_blank\" style=\"display:block;padding:14px 20px;border-radius:10px;background:#2563EB;color:#FFFFFF;text-decoration:none;font-size:15px;font-weight:700;text-align:center;\">Try DraftSpring for $9/mo →</a></td><td width=\"50%\" style=\"padding-left:8px;\"><a href=\"{secondary_cta}\" target=\"_blank\" style=\"display:block;padding:14px 20px;border-radius:10px;background:#EFF6FF;color:#1D4ED8;text-decoration:none;font-size:15px;font-weight:700;text-align:center;border:1px solid #BFDBFE;\">Try the article demo →</a></td></tr></table></td></tr>
<tr><td style=\"padding:0 32px 0 32px;\"><h2 style=\"margin:0 0 14px 0;font-size:18px;line-height:1.3;color:#111827;\">Site-level findings</h2><table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" width=\"100%\">{_render_findings_table(site_findings)}</table></td></tr>
<tr><td style=\"padding:24px 32px 0 32px;\"><hr style=\"border:none;border-top:1px solid #E5E7EB;margin:0;\"></td></tr>{post_section}
<tr><td style=\"padding:24px 32px 0 32px;\"><h2 style=\"margin:0 0 14px 0;font-size:18px;line-height:1.3;color:#111827;\">Top recommendations</h2><ul style=\"margin:0;padding-left:20px;font-size:14px;line-height:1.7;color:#4B5563;\">{recommendations_html or '<li style="margin:0;">Nothing major is broken. Keep publishing consistently and tighten the details.</li>'}</ul></td></tr>
<tr><td style=\"padding:24px 32px 24px 32px;\"><div style=\"padding:18px;border-radius:12px;background:#F3F4F6;font-size:14px;line-height:1.7;color:#4B5563;\">Want to re-run this later? <a href=\"{tool_link}\" target=\"_blank\" style=\"color:#2563EB;text-decoration:none;font-weight:700;\">Use the Ghost Health Check tool again</a>.</div></td></tr>
<tr><td style=\"padding:0 32px 28px 32px;text-align:center;\"><p style=\"margin:0;font-size:12px;color:#9CA3AF;\">DraftSpring · Content automation for Ghost blogs</p></td></tr>
</table>
</td></tr></table>
</body>
</html>"""


async def _send_health_check_report_email(to_email: str, result: dict):
    resend_api_key = os.environ.get("RESEND_API_KEY", "")
    if not resend_api_key:
        logger.warning("health_check_resend_missing")
        return

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {resend_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": "DraftSpring <noreply@draftspring.io>",
                "to": [to_email],
                "subject": f"Your Ghost Health Check score: {result['score']}/100",
                "html": _render_health_check_report_email(result),
            },
        )
        if resp.status_code != 200:
            logger.warning("health_check_report_email_failed", status=resp.status_code, body=resp.text[:200])
            raise RuntimeError(f"Resend email send failed: {resp.status_code}")


@router.post("/health-check/subscribe")
async def subscribe_email(req: SubscribeRequest, request: Request):
    """Proxy email subscription to Mailchimp (avoids CORS issues)."""
    mailchimp_api_key = os.environ.get("MAILCHIMP_API_KEY", "")
    if not mailchimp_api_key:
        return JSONResponse(status_code=503, content={"detail": "Email capture not configured"})

    # Extract datacenter from API key (format: xxx-us18)
    dc = mailchimp_api_key.split("-")[-1] if "-" in mailchimp_api_key else "us18"
    list_id = "ff1f9dd9eb"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://{dc}.api.mailchimp.com/3.0/lists/{list_id}/members",
                auth=("anystring", mailchimp_api_key),
                json={
                    "email_address": req.email,
                    "status": "subscribed",
                    "tags": ["health-check-lead"],
                    "merge_fields": {
                        "DOMAIN": req.url_domain[:50] if req.url_domain else "",
                        "SCORE": str(req.score),
                    },
                },
            )
            if resp.status_code in (200, 201):
                return {"status": "subscribed"}
            elif resp.status_code == 400 and "already a list member" in resp.text.lower():
                return {"status": "already_subscribed"}
            else:
                logger.warning("mailchimp_subscribe_failed", status=resp.status_code, body=resp.text[:200])
                return JSONResponse(status_code=502, content={"detail": "Could not subscribe email"})
    except Exception as e:
        logger.error("mailchimp_error", error=str(e))
        return JSONResponse(status_code=502, content={"detail": "Email service error"})


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@router.get("/health-check")
async def health_check(
    request: Request,
    url: str = Query(..., description="Ghost blog URL to check"),
    email: EmailStr | None = Query(None, description="Optional email to send the report to"),
):
    """Check the content health of a Ghost blog."""
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not _rate_limiter.is_allowed(client_ip):
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded. Try again in a minute."},
        )

    # Validate URL
    clean_url = _validate_url(url)
    if not clean_url:
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid URL. Please provide a valid HTTP(S) URL. Private/local addresses are not allowed."},
        )

    parsed = urlparse(clean_url)
    url_domain = parsed.hostname or ""

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            # Fetch homepage
            try:
                homepage_resp = await client.get(clean_url, headers={"User-Agent": _UA})
                html = homepage_resp.text if homepage_resp.status_code == 200 else ""
            except httpx.TimeoutException:
                return JSONResponse(
                    status_code=504,
                    content={"detail": f"Timeout: {url_domain} took too long to respond. Please try again later."},
                )

            # Check Ghost
            is_ghost = _check_ghost(html)

            # Check homepage SEO
            seo = _check_seo(html)

            # Check homepage structured data
            has_structured_data = _check_structured_data(html)

            # Discover and fetch RSS (try multiple URL patterns)
            rss_data = {"posts_last_30d": 0, "posts_last_60d": 0, "posts_last_90d": 0, "posts_per_month": 0.0, "total_posts": 0, "post_urls": []}
            rss_available = False
            rss_text, rss_url = await _discover_rss_url(client, clean_url)
            if rss_text:
                rss_data = _parse_rss_activity(rss_text)
                rss_available = True

            # Fetch sitemap data
            sitemap_data = await _fetch_sitemap_data(client, clean_url)

            # Merge post count — use the higher of RSS or sitemap
            total_posts = max(rss_data.get("total_posts", 0), sitemap_data.get("total_posts", 0))

            # Collect post URLs from both sources (deduplicated, prefer sitemap order)
            all_post_urls = []
            seen_urls = set()
            for u in sitemap_data.get("post_urls", []):
                if u not in seen_urls:
                    all_post_urls.append(u)
                    seen_urls.add(u)
            for u in rss_data.get("post_urls", []):
                if u not in seen_urls:
                    all_post_urls.append(u)
                    seen_urls.add(u)

            # Check recent post pages (up to 5)
            post_pages = await _check_post_pages(client, all_post_urls, clean_url, max_pages=5)

    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={"detail": f"Timeout: {url_domain} took too long to respond. Please try again later."},
        )
    except Exception as e:
        logger.error("health_check_fetch_error", url=clean_url, error=str(e))
        return JSONResponse(
            status_code=502,
            content={"detail": f"Could not reach {url_domain}. Please check the URL and try again."},
        )

    # Calculate score
    score = _calculate_score(
        is_ghost=is_ghost,
        posts_last_30d=rss_data["posts_last_30d"],
        posts_last_60d=rss_data["posts_last_60d"],
        posts_last_90d=rss_data["posts_last_90d"],
        posts_per_month=rss_data["posts_per_month"],
        title_length=seo["title_length"],
        has_meta_description=seo["has_meta_description"],
        has_og_image=seo["has_og_image"],
        has_structured_data=has_structured_data,
        has_sitemap=sitemap_data.get("has_sitemap", False),
        post_pages=post_pages,
    )

    # Build checks dict for recommendations
    checks = {
        "is_ghost": is_ghost,
        "posts_last_30d": rss_data["posts_last_30d"],
        "posts_last_60d": rss_data["posts_last_60d"],
        "posts_last_90d": rss_data["posts_last_90d"],
        "posts_per_month": rss_data["posts_per_month"],
        "total_posts": total_posts,
        "rss_available": rss_available,
        "title_length": seo["title_length"],
        "title_text": seo.get("title_text", ""),
        "has_meta_description": seo["has_meta_description"],
        "has_og_image": seo["has_og_image"],
        "has_structured_data": has_structured_data,
        "has_sitemap": sitemap_data.get("has_sitemap", False),
        "sitemap_post_count": sitemap_data.get("total_posts", 0),
        "post_pages": post_pages,
    }

    recommendations = _generate_recommendations(checks)

    result = {
        "url": clean_url,
        "url_domain": url_domain,
        "score": score,
        "is_ghost": is_ghost,
        "checks": checks,
        "recommendations": recommendations,
    }

    if email:
        try:
            await _send_health_check_report_email(str(email), result)
        except Exception as e:
            logger.warning("health_check_report_email_error", email=str(email), url=clean_url, error=str(e))

    # PostHog tracking (fire-and-forget)
    _track_posthog(url_domain, score, is_ghost)

    logger.info("health_check_completed", url=clean_url, score=score, is_ghost=is_ghost, emailed=bool(email))

    return result
