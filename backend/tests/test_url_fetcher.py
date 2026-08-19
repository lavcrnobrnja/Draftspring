"""Tests for URL content fetcher."""

import pytest
import httpx

from app.utils.url_fetcher import fetch_url_content, MAX_CHARS


@pytest.mark.asyncio
async def test_fetch_extracts_text(monkeypatch):
    """Should extract readable text from HTML."""
    html = """
    <html><head><title>Test</title><style>body{}</style></head>
    <body>
        <nav>Menu stuff</nav>
        <article>
            <h1>Hello World</h1>
            <p>This is a great article about testing.</p>
            <p>It has multiple paragraphs of content.</p>
        </article>
        <footer>Footer junk</footer>
        <script>alert('hi')</script>
    </body></html>
    """

    async def mock_get(self, url, **kw):
        resp = httpx.Response(200, text=html, request=httpx.Request("GET", url))
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = await fetch_url_content("https://example.com/article")
    assert result["error"] is None
    assert result["extracted_content"] is not None
    # nav, footer, script content should be stripped
    assert "Menu stuff" not in result["extracted_content"]
    assert "Footer junk" not in result["extracted_content"]
    assert "alert" not in result["extracted_content"]
    # Article content should remain
    assert "Hello World" in result["extracted_content"]
    assert "great article about testing" in result["extracted_content"]


@pytest.mark.asyncio
async def test_fetch_truncates_long_content(monkeypatch):
    """Should truncate to MAX_CHARS."""
    long_text = "A" * 5000
    html = f"<html><body><p>{long_text}</p></body></html>"

    async def mock_get(self, url, **kw):
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = await fetch_url_content("https://example.com/long")
    assert result["error"] is None
    assert len(result["extracted_content"]) <= MAX_CHARS + 5  # +5 for ellipsis char


@pytest.mark.asyncio
async def test_fetch_handles_timeout(monkeypatch):
    """Should return error on timeout."""

    async def mock_get(self, url, **kw):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = await fetch_url_content("https://example.com/slow")
    assert result["error"] == "Timeout fetching URL"
    assert result["extracted_content"] is None


@pytest.mark.asyncio
async def test_fetch_handles_http_error(monkeypatch):
    """Should return error on HTTP errors."""

    async def mock_get(self, url, **kw):
        resp = httpx.Response(404, request=httpx.Request("GET", url))
        resp.raise_for_status()

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = await fetch_url_content("https://example.com/missing")
    assert result["error"] is not None
    assert "404" in result["error"]
    assert result["extracted_content"] is None


@pytest.mark.asyncio
async def test_fetch_handles_empty_page(monkeypatch):
    """Should return error when no readable content found."""
    html = "<html><body><script>only scripts</script></body></html>"

    async def mock_get(self, url, **kw):
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = await fetch_url_content("https://example.com/empty")
    assert result["error"] == "No readable content found"
    assert result["extracted_content"] is None


@pytest.mark.asyncio
async def test_fetch_preserves_url(monkeypatch):
    """Should always return the original URL."""
    html = "<html><body><p>Content</p></body></html>"

    async def mock_get(self, url, **kw):
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = await fetch_url_content("https://example.com/test")
    assert result["url"] == "https://example.com/test"
