"""Tests for the Blog Analyzer module."""

import json
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from app.services.blog_analyzer import BlogAnalyzer, BlogProfile, ArticleIdea, BlogAnalyzerError


# ── Fixtures ──────────────────────────────────────────────────────────

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
<title>Test Blog</title>
<link>https://testblog.com</link>
<description>A test blog</description>
{items}
</channel>
</rss>"""


def _make_rss_item(index: int) -> str:
    date = (datetime.now(timezone.utc) - timedelta(days=index * 3)).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )
    return f"""<item>
<title>Post {index}: How to Test Things</title>
<link>https://testblog.com/post-{index}/</link>
<pubDate>{date}</pubDate>
<category>testing</category>
<category>python</category>
<content:encoded><![CDATA[
<p>This is the full content of post {index}. It has multiple paragraphs
and demonstrates the writing style of the blog.</p>
<p>The author tends to use a conversational tone with technical depth.
They often include code examples and real-world analogies. This is post number {index}.</p>
]]></content:encoded>
</item>"""


def _make_rss(count: int = 10) -> str:
    items = "\n".join(_make_rss_item(i) for i in range(1, count + 1))
    return SAMPLE_RSS.format(items=items)


SAMPLE_GEMINI_PROFILE = {
    "topics": ["Python", "Testing", "DevOps"],
    "content_gaps": ["CI/CD", "Monitoring", "Performance"],
    "style_guide": "The author writes in a conversational tone with technical depth. They use short paragraphs and include code examples.",
    "example_sentences": [
        "This is a representative sentence.",
        "Here's another one from the blog.",
        "Testing is not optional.",
        "Deploy early, deploy often.",
        "Let me show you how this works.",
    ],
    "avg_word_count": 1200,
    "publishing_frequency": "2x/week",
}

SAMPLE_GEMINI_IDEAS = [
    {
        "title": "10 Python Testing Patterns You Should Know",
        "angle": "Common patterns that make tests more maintainable",
        "article_type": "listicle",
        "reasoning": "Fits their testing focus with practical depth",
    },
    {
        "title": "Why Your CI Pipeline Is Too Slow",
        "angle": "Identifying and fixing bottlenecks in CI/CD",
        "article_type": "how-to",
        "reasoning": "Fills their CI/CD content gap",
    },
    {
        "title": "The Case Against 100% Code Coverage",
        "angle": "Why chasing coverage metrics can hurt quality",
        "article_type": "opinion",
        "reasoning": "Matches their opinionated writing style",
    },
]


@pytest.fixture
def config():
    """Create a test config."""
    from app.config import Config

    return Config(
        APP_ENV="test",
        DATABASE_PATH=":memory:",
        GEMINI_API_KEY="test-key-1234",
    )


@pytest.fixture
def analyzer(config):
    return BlogAnalyzer(config)


# ── RSS Parsing Tests ─────────────────────────────────────────────────


def test_parse_rss_posts(analyzer):
    """Parse RSS and extract post data."""
    rss = _make_rss(5)
    posts = analyzer._parse_rss_posts(rss)
    assert len(posts) == 5
    assert posts[0]["title"] == "Post 1: How to Test Things"
    assert "testblog.com/post-1" in posts[0]["link"]
    assert posts[0]["date"]  # should have an ISO date
    assert "testing" in posts[0]["tags"]
    assert "python" in posts[0]["tags"]
    assert "content of post 1" in posts[0]["content"]


def test_parse_rss_posts_empty(analyzer):
    """Empty or invalid RSS returns empty list."""
    assert analyzer._parse_rss_posts("") == []
    assert analyzer._parse_rss_posts("not xml") == []


def test_parse_rss_posts_no_content(analyzer):
    """Posts with no content:encoded fall back to description."""
    rss = """<?xml version="1.0"?>
<rss version="2.0">
<channel><title>Blog</title>
<item>
<title>Post A</title>
<link>https://blog.com/a</link>
<description>Short summary</description>
</item>
</channel></rss>"""
    posts = analyzer._parse_rss_posts(rss)
    assert len(posts) == 1
    assert posts[0]["content"] == "Short summary"


def test_extract_site_name(analyzer):
    rss = _make_rss(1)
    assert analyzer._extract_site_name(rss) == "Test Blog"


def test_extract_site_name_fallback(analyzer):
    assert analyzer._extract_site_name("not xml") == "Unknown Blog"


# ── Too Few Posts ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_rejects_fewer_than_3_posts(analyzer):
    """Blogs with < 3 posts are rejected."""
    rss_2_posts = _make_rss(2)

    async def mock_transport(request):
        url = str(request.url)
        if "/rss/" in url:
            return httpx.Response(200, text=rss_2_posts, headers={"content-type": "text/xml"})
        return httpx.Response(404)

    with patch("app.services.blog_analyzer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def mock_get(url, **kwargs):
            if "/rss/" in url:
                return httpx.Response(200, text=rss_2_posts, headers={"content-type": "text/xml"})
            return httpx.Response(404)

        mock_client.get = mock_get
        mock_client_cls.return_value = mock_client

        with pytest.raises(BlogAnalyzerError, match="at least 3"):
            await analyzer.analyze("https://testblog.com")


# ── RSS Discovery ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rss_discovery_tries_multiple_paths(analyzer):
    """If /rss/ fails, try /blog/rss/, /feed/, etc."""
    rss = _make_rss(5)
    attempted_urls = []

    async def mock_get(url, **kwargs):
        attempted_urls.append(str(url))
        if "/feed/" in str(url):
            return httpx.Response(200, text=rss, headers={"content-type": "text/xml"})
        return httpx.Response(404)

    with patch("app.services.blog_analyzer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get
        mock_client_cls.return_value = mock_client

        with patch.object(analyzer, "_extract_profile_via_gemini", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = SAMPLE_GEMINI_PROFILE
            with patch.object(analyzer, "_cache_profile", new_callable=AsyncMock):
                profile = await analyzer.analyze("https://testblog.com")

    assert profile.site_name == "Test Blog"
    # Should have tried /rss/ first, then /blog/rss/, then /feed/ (which succeeded)
    rss_attempts = [u for u in attempted_urls if "/rss" in u or "/feed" in u]
    assert len(rss_attempts) >= 2  # At least tried /rss/ before /feed/


@pytest.mark.asyncio
async def test_tier1_direct_rss_url(analyzer):
    """If user gives a direct RSS URL, Tier 1 uses it immediately."""
    rss = _make_rss(5)
    attempted_urls = []

    async def mock_get(url, **kwargs):
        attempted_urls.append(str(url))
        # The direct RSS URL works; everything else 404s
        if str(url) == "https://lowcode.agency/blog/rss.xml":
            return httpx.Response(200, text=rss, headers={"content-type": "application/rss+xml"})
        return httpx.Response(404)

    with patch("app.services.blog_analyzer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get
        mock_client_cls.return_value = mock_client

        with patch.object(analyzer, "_extract_profile_via_gemini", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = SAMPLE_GEMINI_PROFILE
            with patch.object(analyzer, "_cache_profile", new_callable=AsyncMock):
                profile = await analyzer.analyze("https://www.lowcode.agency/blog/rss.xml")

    assert profile.site_name == "Test Blog"
    # Tier 1 should have found it on the first try (the direct URL)
    assert attempted_urls[0] == "https://lowcode.agency/blog/rss.xml"
    # Profile URL should be the site root, not the RSS URL
    assert profile.url == "https://lowcode.agency"


@pytest.mark.asyncio
async def test_tier2_html_link_autodiscovery(analyzer):
    """If the homepage has a <link rel='alternate'> tag, Tier 2 finds it."""
    rss = _make_rss(5)
    homepage_html = """<html><head>
    <link rel="alternate" type="application/rss+xml" title="Blog" href="/custom-feed.xml">
    </head><body>Hello</body></html>"""

    async def mock_get(url, **kwargs):
        u = str(url)
        if u == "https://testblog.com":
            return httpx.Response(200, text=homepage_html, headers={"content-type": "text/html"})
        if u == "https://testblog.com/custom-feed.xml":
            return httpx.Response(200, text=rss, headers={"content-type": "application/rss+xml"})
        return httpx.Response(404)

    with patch("app.services.blog_analyzer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get
        mock_client_cls.return_value = mock_client

        with patch.object(analyzer, "_extract_profile_via_gemini", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = SAMPLE_GEMINI_PROFILE
            with patch.object(analyzer, "_cache_profile", new_callable=AsyncMock):
                profile = await analyzer.analyze("https://testblog.com")

    assert profile.site_name == "Test Blog"


@pytest.mark.asyncio
async def test_cache_keyed_on_site_root(analyzer):
    """Cache should use site root, not the raw input URL."""
    rss = _make_rss(5)
    cached_urls = []

    async def mock_get(url, **kwargs):
        if str(url).endswith("/rss.xml"):
            return httpx.Response(200, text=rss, headers={"content-type": "application/rss+xml"})
        return httpx.Response(404)

    async def mock_cache(profile):
        cached_urls.append(profile.url)

    with patch("app.services.blog_analyzer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get
        mock_client_cls.return_value = mock_client

        with patch.object(analyzer, "_extract_profile_via_gemini", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = SAMPLE_GEMINI_PROFILE
            with patch.object(analyzer, "_cache_profile", side_effect=mock_cache):
                await analyzer.analyze("https://www.example.com/blog/rss.xml")

    # Cache key should be the site root, not the full RSS URL
    assert cached_urls == ["https://example.com"]


@pytest.mark.asyncio
async def test_no_rss_raises_error(analyzer):
    """No RSS found → error."""
    async def mock_get(url, **kwargs):
        return httpx.Response(404)

    with patch("app.services.blog_analyzer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get
        mock_client_cls.return_value = mock_client

        with pytest.raises(BlogAnalyzerError, match="couldn't find an RSS feed"):
            await analyzer.analyze("https://testblog.com")


# ── Gemini Call Formatting ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gemini_call_uses_correct_model(analyzer):
    """Verify Gemini API call uses gemini-2.5-pro model."""
    captured_url = None

    async def mock_post(url, **kwargs):
        nonlocal captured_url
        captured_url = url
        return httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": json.dumps(SAMPLE_GEMINI_PROFILE)}]}}],
                "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50},
            },
        )

    with patch("app.services.blog_analyzer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = mock_post
        mock_client_cls.return_value = mock_client

        result, usage = await analyzer._gemini_call("system", "user")

    assert "gemini-2.5-pro" in captured_url
    assert "test-key-1234" in captured_url
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50


def test_format_posts_for_llm(analyzer):
    """Posts are formatted with HTML stripped."""
    posts = [
        {
            "title": "Test Post",
            "date": "2026-03-01T12:00:00+00:00",
            "tags": ["python", "testing"],
            "content": "<p>Hello <b>world</b>! This is a test.</p>",
        }
    ]
    result = analyzer._format_posts_for_llm(posts)
    assert "### Post 1: Test Post" in result
    assert "Hello world" in result
    assert "This is a test." in result
    assert "<p>" not in result
    assert "<b>" not in result
    assert "python, testing" in result


# ── Idea Generation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_ideas(analyzer):
    """Generate ideas from a profile."""
    profile = BlogProfile(
        id="test-id",
        url="https://testblog.com",
        site_name="Test Blog",
        is_ghost=True,
        topics=["Python", "Testing"],
        content_gaps=["CI/CD"],
        style_guide="Conversational and technical.",
        example_sentences=["Test sentence."],
        post_summaries=[{"title": "Post 1"}],
    )

    with patch.object(analyzer, "_gemini_call", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = (json.dumps(SAMPLE_GEMINI_IDEAS), {"input_tokens": 50, "output_tokens": 30})
        ideas = await analyzer.generate_ideas(profile, count=3)

    assert len(ideas) == 3
    assert isinstance(ideas[0], ArticleIdea)
    assert ideas[0].title == "10 Python Testing Patterns You Should Know"
    assert ideas[0].article_type == "listicle"
    assert ideas[1].angle == "Identifying and fixing bottlenecks in CI/CD"


# ── Caching ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_analyze_returns_cached(analyzer):
    """get_or_analyze returns cached profile within TTL."""
    cached_profile = BlogProfile(
        id="cached-id",
        url="https://testblog.com",
        site_name="Cached Blog",
        is_ghost=True,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
    )

    with patch.object(analyzer, "_get_cached_profile", new_callable=AsyncMock) as mock_cache:
        mock_cache.return_value = cached_profile
        result = await analyzer.get_or_analyze("https://testblog.com")

    assert result.id == "cached-id"
    assert result.site_name == "Cached Blog"


@pytest.mark.asyncio
async def test_get_or_analyze_reanalyzes_when_stale(analyzer):
    """get_or_analyze re-analyzes when cache is expired."""
    with patch.object(analyzer, "_get_cached_profile", new_callable=AsyncMock) as mock_cache:
        mock_cache.return_value = None  # Cache miss
        with patch.object(analyzer, "analyze", new_callable=AsyncMock) as mock_analyze:
            fresh = BlogProfile(
                id="fresh-id",
                url="https://testblog.com",
                site_name="Fresh Blog",
                is_ghost=True,
            )
            mock_analyze.return_value = fresh
            result = await analyzer.get_or_analyze("https://testblog.com")

    assert result.id == "fresh-id"
    mock_analyze.assert_called_once()


# ── JSON Parsing ──────────────────────────────────────────────────────


def test_parse_json_strips_fences(analyzer):
    raw = '```json\n{"key": "value"}\n```'
    result = analyzer._parse_json_response(raw)
    assert result == {"key": "value"}


def test_parse_json_plain(analyzer):
    raw = '{"key": "value"}'
    result = analyzer._parse_json_response(raw)
    assert result == {"key": "value"}


def test_parse_json_array(analyzer):
    raw = '[{"a": 1}, {"b": 2}]'
    result = analyzer._parse_json_response(raw)
    assert isinstance(result, list)
    assert len(result) == 2


def test_parse_json_invalid_raises(analyzer):
    with pytest.raises(BlogAnalyzerError, match="parse"):
        analyzer._parse_json_response("not json at all")


# ── URL Validation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_analyze_rejects_invalid_url(analyzer):
    with pytest.raises(BlogAnalyzerError, match="Invalid URL"):
        await analyzer.analyze("not-a-url")


@pytest.mark.asyncio
async def test_analyze_rejects_private_ip(analyzer):
    with pytest.raises(BlogAnalyzerError, match="Invalid URL"):
        await analyzer.analyze("http://192.168.1.1")


@pytest.mark.asyncio
async def test_get_or_analyze_rejects_invalid_url(analyzer):
    with pytest.raises(BlogAnalyzerError, match="Invalid URL"):
        await analyzer.get_or_analyze("ftp://bad-scheme.com")


# ── Full Analyze Flow (mocked) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_full_analyze_flow(analyzer):
    """Full analyze() flow with mocked HTTP + Gemini."""
    rss = _make_rss(10)

    async def mock_get(url, **kwargs):
        if "/rss/" in str(url):
            return httpx.Response(200, text=rss, headers={"content-type": "text/xml"})
        return httpx.Response(404)

    with patch("app.services.blog_analyzer.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = mock_get
        mock_client_cls.return_value = mock_client

        with patch.object(analyzer, "_extract_profile_via_gemini", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = SAMPLE_GEMINI_PROFILE
            with patch.object(analyzer, "_cache_profile", new_callable=AsyncMock) as mock_cache:
                profile = await analyzer.analyze("https://testblog.com")

    assert profile.site_name == "Test Blog"
    assert profile.topics == ["Python", "Testing", "DevOps"]
    assert profile.content_gaps == ["CI/CD", "Monitoring", "Performance"]
    assert "conversational" in profile.style_guide.lower()
    assert profile.total_posts == 10
    assert len(profile.post_summaries) == 10
    assert profile.avg_word_count == 1200
    assert profile.publishing_frequency == "2x/week"
    mock_cache.assert_called_once()
