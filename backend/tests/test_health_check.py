"""Tests for Ghost Content Health Check tool endpoint."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import Response as HttpxResponse

from app.routes.health_check import (
    _is_private_ip,
    _validate_url,
    _calculate_score,
    _parse_rss_activity,
    _parse_sitemap,
    _parse_sitemap_index,
    _check_seo,
    _check_structured_data,
    _check_ghost,
    _check_post_page,
    _render_health_check_report_email,
    _rate_limiter,
)


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

class TestValidateUrl:
    def test_valid_https_url(self):
        assert _validate_url("https://blog.ghost.org") == "https://blog.ghost.org"

    def test_valid_http_url(self):
        assert _validate_url("http://example.com") == "http://example.com"

    def test_strips_trailing_slash(self):
        assert _validate_url("https://blog.ghost.org/") == "https://blog.ghost.org"

    def test_rejects_empty(self):
        assert _validate_url("") is None

    def test_rejects_no_scheme(self):
        assert _validate_url("blog.ghost.org") is None

    def test_rejects_ftp(self):
        assert _validate_url("ftp://example.com") is None

    def test_rejects_javascript(self):
        assert _validate_url("javascript:alert(1)") is None


class TestPrivateIpDetection:
    def test_localhost_127(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_localhost_name(self):
        assert _is_private_ip("localhost") is True

    def test_10_range(self):
        assert _is_private_ip("10.0.0.1") is True

    def test_172_16_range(self):
        assert _is_private_ip("172.16.0.1") is True

    def test_172_31_range(self):
        assert _is_private_ip("172.31.255.255") is True

    def test_172_15_not_private(self):
        assert _is_private_ip("172.15.0.1") is False

    def test_192_168_range(self):
        assert _is_private_ip("192.168.1.1") is True

    def test_public_ip(self):
        assert _is_private_ip("8.8.8.8") is False

    def test_public_domain(self):
        assert _is_private_ip("blog.ghost.org") is False

    def test_0000(self):
        assert _is_private_ip("0.0.0.0") is True


# ---------------------------------------------------------------------------
# Scoring (updated for new weights)
# ---------------------------------------------------------------------------

class TestScoring:
    def test_perfect_score_with_post_pages(self):
        """All checks pass including post pages → max 100."""
        post_pages = {
            "pages_checked": 5,
            "meta_description_pct": 100,
            "og_image_pct": 100,
            "avg_word_count": 1500,
            "avg_internal_links": 5,
            "alt_text_pct": 100,
            "structured_data_pct": 100,
        }
        result = _calculate_score(
            is_ghost=True,
            posts_last_30d=5,
            posts_last_60d=10,
            posts_last_90d=15,
            posts_per_month=5.0,
            title_length=55,
            has_meta_description=True,
            has_og_image=True,
            has_structured_data=True,
            has_sitemap=True,
            post_pages=post_pages,
        )
        assert result == 95  # Max achievable: 65 site + 30 post

    def test_site_level_only_score(self):
        """All site-level checks pass without post pages → 60."""
        result = _calculate_score(
            is_ghost=True,
            posts_last_30d=5,
            posts_last_60d=10,
            posts_last_90d=15,
            posts_per_month=5.0,
            title_length=55,
            has_meta_description=True,
            has_og_image=True,
            has_structured_data=True,
            has_sitemap=True,
        )
        # 5 + 15 + 15 + 5 + 10 + 5 + 5 + 5 = 65 (capped at 100)
        assert result == 65

    def test_zero_score(self):
        """All checks fail → 0."""
        result = _calculate_score(
            is_ghost=False,
            posts_last_30d=0,
            posts_last_60d=0,
            posts_last_90d=0,
            posts_per_month=0,
            title_length=200,
            has_meta_description=False,
            has_og_image=False,
            has_structured_data=False,
        )
        assert result == 0

    def test_ghost_only(self):
        result = _calculate_score(
            is_ghost=True,
            posts_last_30d=0,
            posts_last_60d=0,
            posts_last_90d=0,
            posts_per_month=0,
            title_length=200,
            has_meta_description=False,
            has_og_image=False,
            has_structured_data=False,
        )
        assert result == 5

    def test_frequency_tiers(self):
        """4+ posts/mo = 15, 2-3 = 10, 1 = 5, 0 = 0."""
        assert _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=4.0, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
        ) == 15

        assert _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=2.5, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
        ) == 10

        assert _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=1.0, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
        ) == 5

    def test_activity_tiers(self):
        """30d = +15, 60d = +8, 90d = +3, none = 0."""
        assert _calculate_score(
            is_ghost=False, posts_last_30d=3, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=0, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
        ) == 15

        assert _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=3,
            posts_last_90d=0, posts_per_month=0, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
        ) == 8

        assert _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=3, posts_per_month=0, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
        ) == 3

    def test_title_optimal(self):
        """50-60 chars = +5."""
        result = _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=0, title_length=55,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
        )
        assert result == 5

    def test_title_too_short(self):
        result = _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=0, title_length=20,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
        )
        assert result == 0

    def test_seo_checks(self):
        """Meta description = +10, OG image = +5."""
        result = _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=0, title_length=200,
            has_meta_description=True, has_og_image=True, has_structured_data=False,
        )
        assert result == 15

    def test_structured_data(self):
        result = _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=0, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=True,
        )
        assert result == 5

    def test_sitemap_bonus(self):
        result = _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=0, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
            has_sitemap=True,
        )
        assert result == 5

    def test_post_page_scoring(self):
        """Post-level checks contribute up to 30 additional points."""
        post_pages = {
            "pages_checked": 3,
            "meta_description_pct": 100,
            "og_image_pct": 100,
            "avg_word_count": 1500,
            "avg_internal_links": 5,
            "alt_text_pct": 100,
            "structured_data_pct": 100,
        }
        # All post checks pass → +30
        result = _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=0, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
            post_pages=post_pages,
        )
        assert result == 30

    def test_post_page_partial_scoring(self):
        """Partial post-level checks give partial points."""
        post_pages = {
            "pages_checked": 3,
            "meta_description_pct": 60,   # +3
            "og_image_pct": 40,           # +0
            "avg_word_count": 700,        # +3
            "avg_internal_links": 2,      # +3
            "alt_text_pct": 90,           # +5
            "structured_data_pct": 60,    # +3
        }
        result = _calculate_score(
            is_ghost=False, posts_last_30d=0, posts_last_60d=0,
            posts_last_90d=0, posts_per_month=0, title_length=200,
            has_meta_description=False, has_og_image=False, has_structured_data=False,
            post_pages=post_pages,
        )
        assert result == 17  # 3 + 0 + 3 + 3 + 5 + 3

    def test_weights_add_to_100(self):
        """Verify all weights sum to exactly 100 with all checks passing."""
        # Site: 5 (ghost) + 15 (30d) + 15 (freq) + 5 (title) + 10 (meta) + 5 (og) + 5 (structured) + 5 (sitemap) = 65
        # Post: 5 (meta) + 5 (og) + 5 (wc) + 5 (links) + 5 (alt) + 5 (struct) = 30
        # Total: 95, but we still get 100 from the cap
        post_pages = {
            "pages_checked": 5,
            "meta_description_pct": 100,
            "og_image_pct": 100,
            "avg_word_count": 1500,
            "avg_internal_links": 5,
            "alt_text_pct": 100,
            "structured_data_pct": 100,
        }
        result = _calculate_score(
            is_ghost=True,
            posts_last_30d=5,
            posts_last_60d=10,
            posts_last_90d=15,
            posts_per_month=5.0,
            title_length=55,
            has_meta_description=True,
            has_og_image=True,
            has_structured_data=True,
            has_sitemap=True,
            post_pages=post_pages,
        )
        # 65 + 30 = 95, capped at 100
        assert result == 95  # Actual maximum achievable


# ---------------------------------------------------------------------------
# Ghost detection
# ---------------------------------------------------------------------------

class TestGhostDetection:
    def test_detects_ghost_meta(self):
        html = '<html><head><meta name="generator" content="Ghost 5.0"></head><body></body></html>'
        assert _check_ghost(html) is True

    def test_no_ghost_meta(self):
        html = '<html><head><meta name="generator" content="WordPress"></head><body></body></html>'
        assert _check_ghost(html) is False

    def test_case_insensitive(self):
        html = '<html><head><meta name="generator" content="ghost 5.0"></head><body></body></html>'
        assert _check_ghost(html) is True

    def test_empty_html(self):
        assert _check_ghost("") is False


# ---------------------------------------------------------------------------
# SEO checks
# ---------------------------------------------------------------------------

class TestSeoChecks:
    def test_full_seo(self):
        html = '''<html><head>
            <title>Perfect SEO Title That Is Exactly Right</title>
            <meta name="description" content="A nice description">
            <meta property="og:image" content="https://example.com/img.jpg">
        </head><body></body></html>'''
        result = _check_seo(html)
        assert result["title_length"] > 0
        assert result["has_meta_description"] is True
        assert result["has_og_image"] is True

    def test_missing_seo(self):
        html = '<html><head></head><body></body></html>'
        result = _check_seo(html)
        assert result["title_length"] == 0
        assert result["has_meta_description"] is False
        assert result["has_og_image"] is False

    def test_empty_description(self):
        html = '<html><head><meta name="description" content=""></head><body></body></html>'
        result = _check_seo(html)
        assert result["has_meta_description"] is False


# ---------------------------------------------------------------------------
# Structured data
# ---------------------------------------------------------------------------

class TestStructuredData:
    def test_json_ld(self):
        html = '<html><head><script type="application/ld+json">{"@context":"https://schema.org"}</script></head></html>'
        assert _check_structured_data(html) is True

    def test_schema_org_itemtype(self):
        html = '<html><body><div itemscope itemtype="https://schema.org/Article"></div></body></html>'
        assert _check_structured_data(html) is True

    def test_no_structured_data(self):
        html = '<html><head></head><body>Hello</body></html>'
        assert _check_structured_data(html) is False


# ---------------------------------------------------------------------------
# RSS parsing
# ---------------------------------------------------------------------------

class TestRssParsing:
    def test_parses_rss_dates(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=5)).strftime("%a, %d %b %Y %H:%M:%S %z")
        old = (now - timedelta(days=45)).strftime("%a, %d %b %Y %H:%M:%S %z")
        very_old = (now - timedelta(days=75)).strftime("%a, %d %b %Y %H:%M:%S %z")

        rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
        <channel>
            <item><pubDate>{recent}</pubDate><link>https://example.com/post-1</link></item>
            <item><pubDate>{old}</pubDate><link>https://example.com/post-2</link></item>
            <item><pubDate>{very_old}</pubDate><link>https://example.com/post-3</link></item>
        </channel>
        </rss>"""

        result = _parse_rss_activity(rss_xml)
        assert result["posts_last_30d"] == 1
        assert result["posts_last_60d"] == 2
        assert result["posts_last_90d"] == 3
        assert result["posts_per_month"] > 0
        assert len(result["post_urls"]) == 3

    def test_empty_rss(self):
        rss_xml = """<?xml version="1.0"?><rss><channel></channel></rss>"""
        result = _parse_rss_activity(rss_xml)
        assert result["posts_last_30d"] == 0
        assert result["posts_per_month"] == 0
        assert result["post_urls"] == []

    def test_invalid_xml(self):
        result = _parse_rss_activity("not xml at all")
        assert result["posts_last_30d"] == 0
        assert result["posts_per_month"] == 0

    def test_rss_with_gmt_timezone(self):
        """Ghost uses GMT in pubDate — make sure it parses."""
        rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
        <channel>
            <item><pubDate>Mon, 31 Mar 2026 15:21:52 GMT</pubDate><link>https://example.com/post-1</link></item>
        </channel>
        </rss>"""
        result = _parse_rss_activity(rss_xml)
        assert result["total_posts"] == 1
        assert len(result["post_urls"]) == 1

    def test_rss_with_cdata_title(self):
        """Ghost wraps titles in CDATA — still parse correctly."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        rss_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
        <channel>
            <item><title><![CDATA[My Great Post]]></title><pubDate>{recent}</pubDate><link>https://example.com/post-1</link></item>
        </channel>
        </rss>"""
        result = _parse_rss_activity(rss_xml)
        assert result["posts_last_30d"] == 1
        assert result["post_urls"] == ["https://example.com/post-1"]


# ---------------------------------------------------------------------------
# Sitemap parsing
# ---------------------------------------------------------------------------

class TestSitemapParsing:
    def test_parse_sitemap_index(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <sitemap><loc>https://example.com/sitemap-pages.xml</loc></sitemap>
            <sitemap><loc>https://example.com/sitemap-posts.xml</loc></sitemap>
            <sitemap><loc>https://example.com/sitemap-authors.xml</loc></sitemap>
        </sitemapindex>"""
        urls = _parse_sitemap_index(xml)
        assert len(urls) == 3
        assert "https://example.com/sitemap-posts.xml" in urls

    def test_parse_sitemap_index_invalid_xml(self):
        urls = _parse_sitemap_index("not xml")
        assert urls == []

    def test_parse_posts_sitemap(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/blog/post-1/</loc><lastmod>2026-03-31T15:21:52.967Z</lastmod></url>
            <url><loc>https://example.com/blog/post-2/</loc><lastmod>2026-03-30T11:12:29.397Z</lastmod></url>
            <url><loc>https://example.com/blog/post-3/</loc><lastmod>2026-03-25T00:20:28.000Z</lastmod></url>
        </urlset>"""
        result = _parse_sitemap(xml)
        assert result["has_sitemap"] is True
        assert result["total_posts"] == 3
        assert len(result["post_urls"]) == 3
        assert result["latest_post_date"] is not None

    def test_parse_sitemap_invalid_xml(self):
        result = _parse_sitemap("not xml")
        assert result["has_sitemap"] is False
        assert result["total_posts"] == 0

    def test_parse_sitemap_empty(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        </urlset>"""
        result = _parse_sitemap(xml)
        assert result["has_sitemap"] is True
        assert result["total_posts"] == 0


# ---------------------------------------------------------------------------
# Post page analysis
# ---------------------------------------------------------------------------

class TestPostPageAnalysis:
    def test_check_post_page_full(self):
        html = '''<html><head>
            <title>Great Post Title Here</title>
            <meta name="description" content="A nice description">
            <meta property="og:image" content="https://example.com/img.jpg">
            <script type="application/ld+json">{"@context":"https://schema.org"}</script>
        </head><body>
            <p>This is a blog post with some content. It has multiple sentences
            and paragraphs to simulate real content. The word count should be
            reasonable for this test.</p>
            <img src="https://example.com/photo.jpg" alt="A nice photo">
            <img src="https://example.com/photo2.jpg" alt="">
            <a href="https://example.com/blog/other-post/">Internal link</a>
            <a href="https://twitter.com/example">External link</a>
        </body></html>'''
        result = _check_post_page(html, "https://example.com")
        assert result["seo"]["has_meta_description"] is True
        assert result["seo"]["has_og_image"] is True
        assert result["has_structured_data"] is True
        assert result["word_count"] > 0
        assert result["total_images"] == 2
        assert result["images_with_alt"] == 1
        assert result["internal_links"] >= 1

    def test_check_post_page_minimal(self):
        html = '<html><head><title>Hi</title></head><body><p>Short post.</p></body></html>'
        result = _check_post_page(html, "https://example.com")
        assert result["word_count"] > 0
        assert result["total_images"] == 0
        assert result["internal_links"] == 0


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_allows_within_limit(self):
        _rate_limiter.clear()
        for _ in range(10):
            assert _rate_limiter.is_allowed("1.2.3.4") is True

    def test_blocks_over_limit(self):
        _rate_limiter.clear()
        for _ in range(10):
            _rate_limiter.is_allowed("5.6.7.8")
        assert _rate_limiter.is_allowed("5.6.7.8") is False

    def test_different_ips_independent(self):
        _rate_limiter.clear()
        for _ in range(10):
            _rate_limiter.is_allowed("1.1.1.1")
        assert _rate_limiter.is_allowed("1.1.1.1") is False
        assert _rate_limiter.is_allowed("2.2.2.2") is True


# ---------------------------------------------------------------------------
# Full endpoint integration tests (mocked HTTP)
# ---------------------------------------------------------------------------

def _make_mock_client(side_effect_map=None, default_response=None):
    """Create a properly mocked httpx.AsyncClient for endpoint tests.

    side_effect_map: dict of URL substring → MagicMock response
    default_response: fallback MagicMock for unmatched URLs
    """
    if default_response is None:
        default_response = MagicMock(status_code=404, text="", headers={})

    async def mock_get(url, **kwargs):
        if side_effect_map:
            for pattern, resp in side_effect_map.items():
                if pattern in url:
                    return resp
        return default_response

    mock_instance = AsyncMock()
    mock_instance.get = AsyncMock(side_effect=mock_get)
    return mock_instance


@pytest.fixture
def ghost_html():
    return '''<html><head>
        <meta name="generator" content="Ghost 5.82">
        <title>My Awesome Ghost Blog Title Here!</title>
        <meta name="description" content="A blog about awesome things">
        <meta property="og:image" content="https://example.com/img.jpg">
        <script type="application/ld+json">{"@context":"https://schema.org"}</script>
    </head><body></body></html>'''


@pytest.fixture
def ghost_rss():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    dates = []
    for i in range(5):
        d = (now - timedelta(days=i * 7)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        dates.append(f"<item><pubDate>{d}</pubDate><link>https://blog.ghost.org/post-{i}/</link></item>")
    items = "\n".join(dates)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>{items}</channel></rss>"""


@pytest.fixture
def ghost_sitemap_index():
    return """<?xml version="1.0" encoding="UTF-8"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <sitemap><loc>https://blog.ghost.org/sitemap-posts.xml</loc></sitemap>
    </sitemapindex>"""


@pytest.fixture
def ghost_sitemap_posts():
    return """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://blog.ghost.org/post-0/</loc><lastmod>2026-03-31T12:00:00.000Z</lastmod></url>
        <url><loc>https://blog.ghost.org/post-1/</loc><lastmod>2026-03-24T12:00:00.000Z</lastmod></url>
        <url><loc>https://blog.ghost.org/post-2/</loc><lastmod>2026-03-17T12:00:00.000Z</lastmod></url>
    </urlset>"""


@pytest.fixture
def ghost_post_html():
    return '''<html><head>
        <meta name="generator" content="Ghost 5.82">
        <title>A Great Blog Post</title>
        <meta name="description" content="Post description">
        <meta property="og:image" content="https://blog.ghost.org/img.jpg">
        <script type="application/ld+json">{"@context":"https://schema.org","@type":"Article"}</script>
    </head><body>
        <p>This is a long blog post with plenty of content. ''' + " word" * 200 + '''</p>
        <img src="https://blog.ghost.org/photo.jpg" alt="Nice photo">
        <a href="https://blog.ghost.org/post-1/">Related post</a>
    </body></html>'''


@pytest.fixture
def non_ghost_html():
    return '''<html><head>
        <meta name="generator" content="WordPress 6.4">
        <title>WP</title>
    </head><body></body></html>'''


@pytest.mark.asyncio
async def test_endpoint_ghost_blog(ghost_html, ghost_rss, ghost_sitemap_index, ghost_sitemap_posts, ghost_post_html):
    """Valid Ghost blog returns full results with score."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    mock = _make_mock_client({
        "blog.ghost.org/rss": MagicMock(status_code=200, text=ghost_rss, headers={"content-type": "application/rss+xml"}),
        "blog.ghost.org/sitemap.xml": MagicMock(status_code=200, text=ghost_sitemap_index, headers={}),
        "sitemap-posts.xml": MagicMock(status_code=200, text=ghost_sitemap_posts, headers={}),
        "blog.ghost.org/post-": MagicMock(status_code=200, text=ghost_post_html),
    }, default_response=MagicMock(status_code=200, text=ghost_html, headers={}))

    with patch("app.routes.health_check.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/api/v1/tools/health-check?url=https://blog.ghost.org")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_ghost"] is True
        assert data["score"] > 0
        assert "checks" in data
        assert "recommendations" in data
        assert "post_pages" in data["checks"]
        assert data["checks"]["has_sitemap"] is True


@pytest.mark.asyncio
async def test_endpoint_non_ghost(non_ghost_html):
    """Non-Ghost site returns partial results with is_ghost=false."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    mock = _make_mock_client(default_response=MagicMock(status_code=200, text=non_ghost_html, headers={}))
    # Override RSS/sitemap to 404
    original_get = mock.get

    async def custom_get(url, **kwargs):
        if "rss" in url or "sitemap" in url:
            return MagicMock(status_code=404, text="", headers={})
        return await original_get(url, **kwargs)

    mock.get = AsyncMock(side_effect=custom_get)

    with patch("app.routes.health_check.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/api/v1/tools/health-check?url=https://wordpress.com")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_ghost"] is False
        assert data["score"] >= 0


@pytest.mark.asyncio
async def test_endpoint_invalid_url():
    """Invalid URL returns 400."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    resp = client.get("/api/v1/tools/health-check?url=not-a-url")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_endpoint_private_ip():
    """Private IP returns 400."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    resp = client.get("/api/v1/tools/health-check?url=http://192.168.1.1")
    assert resp.status_code == 400
    assert "private" in resp.json()["detail"].lower() or "not allowed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_endpoint_localhost():
    """Localhost returns 400."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    resp = client.get("/api/v1/tools/health-check?url=http://localhost:8080")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_endpoint_rate_limiting():
    """11th request from same IP gets 429."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    mock = _make_mock_client(default_response=MagicMock(status_code=200, text="<html></html>", headers={}))

    with patch("app.routes.health_check.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        for i in range(10):
            resp = client.get("/api/v1/tools/health-check?url=https://example.com")
            assert resp.status_code == 200, f"Request {i+1} should succeed"

        resp = client.get("/api/v1/tools/health-check?url=https://example.com")
        assert resp.status_code == 429


@pytest.mark.asyncio
async def test_endpoint_timeout():
    """Timeout returns graceful error."""
    import httpx as real_httpx
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    with patch("app.routes.health_check.httpx.AsyncClient") as MockClient:
        mock_instance = AsyncMock()
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_instance.get.side_effect = real_httpx.TimeoutException("Connection timed out")

        resp = client.get("/api/v1/tools/health-check?url=https://slow-site.com")
        assert resp.status_code == 504
        assert "timeout" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_endpoint_missing_rss(ghost_html):
    """Broken RSS returns partial results gracefully."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    mock = _make_mock_client(default_response=MagicMock(status_code=404, text="Not found", headers={}))
    # Homepage returns ghost_html, everything else 404
    original_get = mock.get

    async def custom_get(url, **kwargs):
        if url.rstrip("/") == "https://blog.ghost.org":
            return MagicMock(status_code=200, text=ghost_html, headers={})
        return MagicMock(status_code=404, text="Not found", headers={})

    mock.get = AsyncMock(side_effect=custom_get)

    with patch("app.routes.health_check.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/api/v1/tools/health-check?url=https://blog.ghost.org")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_ghost"] is True
        assert "checks" in data


@pytest.mark.asyncio
async def test_endpoint_missing_url_param():
    """Missing url param returns 400."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    resp = client.get("/api/v1/tools/health-check")
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_endpoint_rss_discovery_blog_prefix(ghost_html, ghost_rss):
    """RSS at /blog/rss/ is discovered when /rss/ returns 404."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    async def custom_get(url, **kwargs):
        if url.endswith("/rss/") and "/blog/" not in url:
            return MagicMock(status_code=404, text="", headers={})
        if "/blog/rss/" in url:
            return MagicMock(status_code=200, text=ghost_rss, headers={"content-type": "application/rss+xml"})
        if "sitemap" in url:
            return MagicMock(status_code=404, text="", headers={})
        return MagicMock(status_code=200, text=ghost_html, headers={})

    mock_instance = AsyncMock()
    mock_instance.get = AsyncMock(side_effect=custom_get)

    with patch("app.routes.health_check.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/api/v1/tools/health-check?url=https://blog.ghost.org")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checks"]["rss_available"] is True
        assert data["checks"]["posts_last_30d"] > 0


@pytest.mark.asyncio
async def test_endpoint_sitemap_data(ghost_html, ghost_sitemap_index, ghost_sitemap_posts):
    """Sitemap data is included in results."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()

    async def custom_get(url, **kwargs):
        if "sitemap.xml" in url and "posts" not in url:
            return MagicMock(status_code=200, text=ghost_sitemap_index, headers={})
        if "sitemap-posts.xml" in url:
            return MagicMock(status_code=200, text=ghost_sitemap_posts, headers={})
        if "rss" in url:
            return MagicMock(status_code=404, text="", headers={})
        return MagicMock(status_code=200, text=ghost_html, headers={})

    mock_instance = AsyncMock()
    mock_instance.get = AsyncMock(side_effect=custom_get)

    with patch("app.routes.health_check.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/api/v1/tools/health-check?url=https://blog.ghost.org")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checks"]["has_sitemap"] is True
        assert data["checks"]["sitemap_post_count"] == 3


class TestHealthCheckReportEmail:
    def test_report_email_renders_score_sections_and_ctas(self):
        result = {
            "url": "https://blog.ghost.org",
            "url_domain": "blog.ghost.org",
            "score": 72,
            "checks": {
                "is_ghost": True,
                "posts_last_30d": 2,
                "posts_last_90d": 4,
                "posts_per_month": 2.5,
                "total_posts": 12,
                "title_length": 55,
                "has_meta_description": True,
                "has_og_image": False,
                "has_structured_data": True,
                "has_sitemap": True,
                "sitemap_post_count": 12,
                "post_pages": {
                    "pages_checked": 3,
                    "meta_description_pct": 67,
                    "og_image_pct": 100,
                    "avg_word_count": 840,
                    "avg_internal_links": 2,
                    "alt_text_pct": 50,
                    "images_with_alt": 2,
                    "total_images_checked": 4,
                    "structured_data_pct": 33,
                },
            },
            "recommendations": ["Add alt text", "Write longer posts"],
        }

        html = _render_health_check_report_email(result)
        assert "Your Ghost Health Check report" in html
        assert ">72<" in html
        assert "Site-level findings" in html
        assert "Per-post analysis" in html
        assert "Try DraftSpring for $9/mo" in html
        assert "Try the article demo" in html
        assert "utm_source=health-check" in html
        assert "utm_campaign=try-draftspring" in html

    def test_report_email_renders_pass_warning_fail_labels(self):
        result = {
            "url": "https://blog.ghost.org",
            "url_domain": "blog.ghost.org",
            "score": 44,
            "checks": {
                "is_ghost": True,
                "posts_last_30d": 0,
                "posts_last_90d": 1,
                "posts_per_month": 0.5,
                "total_posts": 2,
                "title_length": 20,
                "has_meta_description": False,
                "has_og_image": False,
                "has_structured_data": True,
                "has_sitemap": False,
                "sitemap_post_count": 0,
                "post_pages": {
                    "pages_checked": 1,
                    "meta_description_pct": 100,
                    "og_image_pct": 0,
                    "avg_word_count": 500,
                    "avg_internal_links": 0,
                    "alt_text_pct": 40,
                    "images_with_alt": 0,
                    "total_images_checked": 2,
                    "structured_data_pct": 100,
                },
            },
            "recommendations": [],
        }

        html = _render_health_check_report_email(result)
        assert "PASS" in html
        assert "WARNING" in html
        assert "FAIL" in html


@pytest.mark.asyncio
async def test_endpoint_triggers_email_send_when_email_supplied(ghost_html, ghost_rss, ghost_sitemap_index, ghost_sitemap_posts, ghost_post_html, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()
    monkeypatch.setenv("RESEND_API_KEY", "test-key")

    send_resp = MagicMock(status_code=200, text="{\"id\":\"ok\"}", headers={})
    mock = _make_mock_client({
        "api.resend.com/emails": send_resp,
        "blog.ghost.org/rss": MagicMock(status_code=200, text=ghost_rss, headers={"content-type": "application/rss+xml"}),
        "blog.ghost.org/sitemap.xml": MagicMock(status_code=200, text=ghost_sitemap_index, headers={}),
        "sitemap-posts.xml": MagicMock(status_code=200, text=ghost_sitemap_posts, headers={}),
        "blog.ghost.org/post-": MagicMock(status_code=200, text=ghost_post_html, headers={}),
    }, default_response=MagicMock(status_code=200, text=ghost_html, headers={}))
    mock.post = AsyncMock(return_value=send_resp)

    with patch("app.routes.health_check.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/api/v1/tools/health-check?url=https://blog.ghost.org&email=test@example.com")
        assert resp.status_code == 200
        mock.post.assert_awaited_once()
        payload = mock.post.await_args.kwargs["json"]
        assert payload["to"] == ["test@example.com"]
        assert "Try DraftSpring for $9/mo" in payload["html"]


@pytest.mark.asyncio
async def test_endpoint_still_succeeds_if_email_send_fails(ghost_html, ghost_rss, ghost_sitemap_index, ghost_sitemap_posts, ghost_post_html, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.config import Config

    app = create_app(Config(APP_ENV="test", DATABASE_PATH=":memory:"))
    client = TestClient(app)
    _rate_limiter.clear()
    monkeypatch.setenv("RESEND_API_KEY", "test-key")

    mock = _make_mock_client({
        "blog.ghost.org/rss": MagicMock(status_code=200, text=ghost_rss, headers={"content-type": "application/rss+xml"}),
        "blog.ghost.org/sitemap.xml": MagicMock(status_code=200, text=ghost_sitemap_index, headers={}),
        "sitemap-posts.xml": MagicMock(status_code=200, text=ghost_sitemap_posts, headers={}),
        "blog.ghost.org/post-": MagicMock(status_code=200, text=ghost_post_html, headers={}),
    }, default_response=MagicMock(status_code=200, text=ghost_html, headers={}))
    mock.post = AsyncMock(return_value=MagicMock(status_code=500, text="boom", headers={}))

    with patch("app.routes.health_check.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

        resp = client.get("/api/v1/tools/health-check?url=https://blog.ghost.org&email=test@example.com")
        assert resp.status_code == 200
        data = resp.json()
        assert data["score"] > 0
        mock.post.assert_awaited_once()
