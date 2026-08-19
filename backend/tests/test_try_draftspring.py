"""Tests for the Try DraftSpring demo tool endpoints."""

import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
async def app_instance(tmp_path):
    """Create a test app with in-memory DB."""
    import os
    os.environ.setdefault("APP_ENV", "test")

    db_path = str(tmp_path / "test.db")
    os.environ["DATABASE_PATH"] = db_path

    os.environ["TURNSTILE_SECRET_KEY"] = ""  # Disable captcha in tests
    app = create_app()
    # Run lifespan
    async with app.router.lifespan_context(app):
        app.state.config.DATABASE_PATH = db_path
        app.state.config.TURNSTILE_SECRET_KEY = ""
        yield app


@pytest.fixture
async def client(app_instance):
    """Create a test client."""
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def clear_rate_limiter():
    """Clear rate limiter between tests."""
    from app.routes.try_draftspring import _rate_limiter
    _rate_limiter.clear()
    yield
    _rate_limiter.clear()


# ── Validation Tests ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_url(client):
    """Missing URL returns 422."""
    resp = await client.post(
        "/api/v1/tools/try-draftspring",
        json={"email": "test@example.com"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_missing_email(client):
    """Missing email returns 422."""
    resp = await client.post(
        "/api/v1/tools/try-draftspring",
        json={"url": "https://example.com"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_email(client):
    """Invalid email returns 422."""
    resp = await client.post(
        "/api/v1/tools/try-draftspring",
        json={"url": "https://example.com", "email": "not-an-email"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_url(client):
    """Invalid URL returns 400."""
    resp = await client.post(
        "/api/v1/tools/try-draftspring",
        json={"url": "not-a-url", "email": "test@example.com"},
    )
    assert resp.status_code == 400
    assert "publicly accessible" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_private_ip_rejected(client):
    """Private IP addresses are rejected."""
    resp = await client.post(
        "/api/v1/tools/try-draftspring",
        json={"url": "http://192.168.1.1", "email": "test@example.com"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_localhost_rejected(client):
    """Localhost is rejected."""
    resp = await client.post(
        "/api/v1/tools/try-draftspring",
        json={"url": "http://localhost:2368", "email": "test@example.com"},
    )
    assert resp.status_code == 400


# ── Rate Limiting ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limiting(client):
    """Rate limit after 5 requests per hour."""
    # Need to mock the pipeline so it doesn't actually run
    with patch("app.routes.try_draftspring._run_pipeline", new_callable=AsyncMock):
        for i in range(5):
            resp = await client.post(
                "/api/v1/tools/try-draftspring",
                json={"url": "https://example.com", "email": f"user{i}@example.com"},
            )
            assert resp.status_code == 200, f"Request {i+1} should succeed"

        # 6th request should be rate limited
        resp = await client.post(
            "/api/v1/tools/try-draftspring",
            json={"url": "https://example.com", "email": "user6@example.com"},
        )
        assert resp.status_code == 429
        assert "Too many requests" in resp.json()["detail"]


# ── Email Uniqueness ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_email_uniqueness(client):
    """Same email returns 409."""
    with patch("app.routes.try_draftspring._run_pipeline", new_callable=AsyncMock):
        # First request succeeds
        resp1 = await client.post(
            "/api/v1/tools/try-draftspring",
            json={"url": "https://example.com", "email": "dupe@example.com"},
        )
        assert resp1.status_code == 200

        # Second request with same email returns 409 (generation in progress)
        resp2 = await client.post(
            "/api/v1/tools/try-draftspring",
            json={"url": "https://example.com", "email": "dupe@example.com"},
        )
        assert resp2.status_code == 409
        # Record is in 'pending' state, so it returns the in-progress message
        assert "being generated" in resp2.json()["detail"]


# ── Retry Behavior ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_allowed_after_failed(client, app_instance):
    """Retry is allowed when the previous attempt has task_status == 'failed'."""
    from app.database import get_connection
    import uuid

    config = app_instance.state.config
    email = "retry-failed@example.com"
    task_id = str(uuid.uuid4())

    # Seed a failed record directly
    async with get_connection(config.DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO demo_articles (id, email, task_status, stage_message) VALUES (?, ?, 'failed', 'Analysis failed')",
            (task_id, email),
        )
        await db.commit()

    # Retry should succeed (not 409)
    with patch("app.routes.try_draftspring._run_pipeline", new_callable=AsyncMock):
        resp = await client.post(
            "/api/v1/tools/try-draftspring",
            json={"url": "https://example.com", "email": email},
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
    new_task_id = resp.json()["task_id"]
    # Should be a new task, not the old one
    assert new_task_id != task_id

    # Old record should be deleted
    async with get_connection(config.DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT id FROM demo_articles WHERE id = ?", (task_id,)
        )
        assert len(rows) == 0, "Old failed record should have been deleted"


@pytest.mark.asyncio
async def test_retry_blocked_after_complete(client, app_instance):
    """Retry is blocked with 409 when the previous attempt has task_status == 'complete'."""
    from app.database import get_connection
    import uuid

    config = app_instance.state.config
    email = "retry-complete@example.com"
    task_id = str(uuid.uuid4())

    # Seed a completed record
    async with get_connection(config.DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO demo_articles (id, email, task_status, stage_message) VALUES (?, ?, 'complete', 'Done!')",
            (task_id, email),
        )
        await db.commit()

    resp = await client.post(
        "/api/v1/tools/try-draftspring",
        json={"url": "https://example.com", "email": email},
    )

    assert resp.status_code == 409
    data = resp.json()
    assert "already generated" in data["detail"]
    assert data["task_id"] == task_id


@pytest.mark.asyncio
async def test_retry_blocked_while_in_progress(client, app_instance):
    """Retry is blocked with 409 when a generation is in progress (pending/scanning/etc.)."""
    from app.database import get_connection
    import uuid

    config = app_instance.state.config

    from app.routes.try_draftspring import _rate_limiter

    for in_progress_status in ["pending", "scanning", "analyzing", "ideating", "drafting", "imaging", "sending"]:
        _rate_limiter.clear()  # reset per-iteration to avoid hitting the 5 req/hr limit
        email = f"inprogress-{in_progress_status}@example.com"
        task_id = str(uuid.uuid4())

        async with get_connection(config.DATABASE_PATH) as db:
            await db.execute(
                "INSERT INTO demo_articles (id, email, task_status, stage_message) VALUES (?, ?, ?, 'In progress')",
                (task_id, email, in_progress_status),
            )
            await db.commit()

        resp = await client.post(
            "/api/v1/tools/try-draftspring",
            json={"url": "https://example.com", "email": email},
        )

        assert resp.status_code == 409, f"Expected 409 for status '{in_progress_status}', got {resp.status_code}"
        data = resp.json()
        assert "being generated" in data["detail"], f"Expected in-progress message for status '{in_progress_status}'"
        assert data["task_id"] == task_id


# ── Successful Submission ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_submission(client):
    """Valid submission returns task_id and starts pipeline."""
    with patch("app.routes.try_draftspring._run_pipeline", new_callable=AsyncMock) as mock_pipeline:
        resp = await client.post(
            "/api/v1/tools/try-draftspring",
            json={"url": "https://myblog.com", "email": "valid@example.com"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data
    assert len(data["task_id"]) == 36  # UUID format


# ── Status Endpoint ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_not_found(client):
    """Non-existent task returns 404."""
    resp = await client.get("/api/v1/tools/try-draftspring/non-existent-id/status")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_status_pending(client):
    """Pending task returns correct status."""
    with patch("app.routes.try_draftspring._run_pipeline", new_callable=AsyncMock):
        resp = await client.post(
            "/api/v1/tools/try-draftspring",
            json={"url": "https://example.com", "email": "status@example.com"},
        )
        task_id = resp.json()["task_id"]

    status_resp = await client.get(f"/api/v1/tools/try-draftspring/{task_id}/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["status"] == "pending"
    assert data["stage_message"] == "Starting..."
    assert data["result"] is None


# ── Task Status Transitions ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_updates(app_instance):
    """Verify _update_status correctly updates DB."""
    from app.routes.try_draftspring import _update_status
    from app.database import get_connection

    config = app_instance.state.config
    task_id = "test-status-id"

    # Insert a demo_articles row
    async with get_connection(config.DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO demo_articles (id, email, task_status, stage_message) VALUES (?, ?, 'pending', 'Starting...')",
            (task_id, "status-test@example.com"),
        )
        await db.commit()

    # Update to scanning
    await _update_status(config, task_id, "scanning", "Scanning your blog...")

    async with get_connection(config.DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT task_status, stage_message FROM demo_articles WHERE id = ?",
            (task_id,),
        )
        assert rows[0]["task_status"] == "scanning"
        assert rows[0]["stage_message"] == "Scanning your blog..."

    # Update with extras
    await _update_status(
        config, task_id, "complete", "Done!",
        idea_title="Test Title",
        cover_image_url="https://example.com/cover.png",
    )

    async with get_connection(config.DATABASE_PATH) as db:
        rows = await db.execute_fetchall(
            "SELECT task_status, idea_title, cover_image_url FROM demo_articles WHERE id = ?",
            (task_id,),
        )
        assert rows[0]["task_status"] == "complete"
        assert rows[0]["idea_title"] == "Test Title"
        assert rows[0]["cover_image_url"] == "https://example.com/cover.png"


# ── Ghost Validation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_ghost_success():
    """_validate_ghost returns True for Ghost blogs."""
    from app.routes.try_draftspring import _validate_ghost

    ghost_html = '<html><head><meta name="generator" content="Ghost 5.0"></head></html>'
    with patch("app.routes.try_draftspring.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            return_value=MagicMock(status_code=200, text=ghost_html)
        )
        mock_cls.return_value = mock_client

        assert await _validate_ghost("https://ghost-blog.com") is True


@pytest.mark.asyncio
async def test_validate_ghost_failure():
    """_validate_ghost returns False for non-Ghost sites."""
    from app.routes.try_draftspring import _validate_ghost

    non_ghost_html = "<html><head><title>WordPress Blog</title></head></html>"
    with patch("app.routes.try_draftspring.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(
            return_value=MagicMock(status_code=200, text=non_ghost_html)
        )
        mock_cls.return_value = mock_client

        assert await _validate_ghost("https://wordpress-blog.com") is False


# ── Markdown Conversion ──────────────────────────────────────────────

def test_markdown_to_html():
    """Markdown is converted to clean HTML."""
    from app.routes.try_draftspring import _markdown_to_html

    md = """# Title

This is a paragraph with **bold** and *italic* text.

## Section One

- Item one
- Item two

Another paragraph here.

[IMAGE_ANCHOR:COVER]

### Subsection

Final paragraph with a [link](https://example.com)."""

    html = _markdown_to_html(md)
    assert "<h1>Title</h1>" in html
    assert "<h2>Section One</h2>" in html
    assert "<h3>Subsection</h3>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<ul>" in html
    assert "<li>Item one</li>" in html
    assert "IMAGE_ANCHOR" not in html
    assert '<a href="https://example.com">link</a>' in html


def test_markdown_to_html_h1_stripping_for_email():
    """Leading H1 is stripped from article HTML before email template insertion.

    The email template already renders the title in its own styled H1,
    so the article body's leading H1 must be removed to avoid duplication.
    """
    import re
    from app.routes.try_draftspring import _markdown_to_html

    md = """# My Article Title

This is the first paragraph.

## Section Two

More content here."""

    article_html = _markdown_to_html(md)
    # The raw conversion DOES produce an H1
    assert "<h1>My Article Title</h1>" in article_html

    # But the pipeline strips it before email insertion
    stripped = re.sub(r"^\s*<h1>.*?</h1>\s*", "", article_html, count=1)
    assert "<h1>" not in stripped
    assert "<h2>Section Two</h2>" in stripped
    assert "first paragraph" in stripped


def test_markdown_to_html_no_h1():
    """Articles without leading H1 are unaffected by the stripping logic."""
    import re
    from app.routes.try_draftspring import _markdown_to_html

    md = """This is a paragraph.

## Section One

More content."""

    article_html = _markdown_to_html(md)
    stripped = re.sub(r"^\s*<h1>.*?</h1>\s*", "", article_html, count=1)
    assert "<h2>Section One</h2>" in stripped
    assert "This is a paragraph" in stripped


def test_extract_preview():
    """Preview extracts first paragraph."""
    from app.routes.try_draftspring import _extract_preview

    md = """# My Article Title

[IMAGE_ANCHOR:COVER]

This is the first real paragraph of the article with some content.

And this is the second paragraph."""

    preview = _extract_preview(md)
    assert "first real paragraph" in preview
    assert "second paragraph" not in preview


# ── Email Template ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_email_template():
    """Email HTML is well-formed with required elements."""
    from app.routes.try_draftspring import _send_article_email
    from app.config import Config

    config = Config(
        APP_ENV="test",
        RESEND_API_KEY="test-key",
        EMAIL_FROM_ADDRESS="test@example.com",
        EMAIL_FROM_NAME="Test",
    )

    captured_json = None

    class MockResponse:
        status_code = 200

    class MockClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, url, **kwargs):
            nonlocal captured_json
            captured_json = kwargs.get("json", {})
            return MockResponse()

    with patch("app.routes.try_draftspring.httpx.AsyncClient", return_value=MockClient()):
        await _send_article_email(
            config,
            "user@example.com",
            "Test Article Title",
            "<p>Article body content here.</p>",
            "https://cdn.example.com/cover.png",
        )

    assert captured_json is not None
    html = captured_json["html"]
    assert "Test Article Title" in html
    assert "Article body content here" in html
    assert "https://cdn.example.com/cover.png" in html
    assert "Try DraftSpring for $9/mo" in html
    assert "utm_source=try-draftspring" in html
    assert captured_json["from"] == "DraftSpring <noreply@draftspring.io>"


@pytest.mark.asyncio
async def test_email_without_cover_image():
    """Email works without cover image."""
    from app.routes.try_draftspring import _send_article_email
    from app.config import Config

    config = Config(APP_ENV="test", RESEND_API_KEY="test-key")

    captured_json = None

    class MockResponse:
        status_code = 200

    class MockClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, url, **kwargs):
            nonlocal captured_json
            captured_json = kwargs.get("json", {})
            return MockResponse()

    with patch("app.routes.try_draftspring.httpx.AsyncClient", return_value=MockClient()):
        await _send_article_email(
            config, "user@example.com", "No Cover", "<p>Body</p>", None
        )

    assert captured_json is not None
    html = captured_json["html"]
    assert "No Cover" in html
    # No img tag for cover
    assert 'alt="Cover image"' not in html
