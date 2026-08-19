"""Task 1.6: Settings endpoint tests."""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.database import get_connection, run_migrations
from app.models.user import create_user
from app.middleware.auth_middleware import create_session
from app.services.email import clear_sent_emails


@pytest.fixture
def app(config):
    return create_app(config)


@pytest_asyncio.fixture
async def authed_client(app, config):
    """Client with a valid full session."""
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
        user = await create_user(db, "settings@test.com")
        session_id = await create_session(db, user["id"], "full")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        clear_sent_emails()
        from app.routes.auth import _rate_limit
        _rate_limit.clear()
        yield ac


@pytest.mark.asyncio
async def test_get_settings(authed_client):
    """GET /api/settings returns user config."""
    resp = await authed_client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "email" in data
    assert data["email"] == "settings@test.com"
    assert data["image_style"] == "photography"
    assert data["image_substyle"] == "editorial_documentary"


@pytest.mark.asyncio
async def test_settings_never_returns_api_key(authed_client):
    """Settings response must never include the raw API key."""
    resp = await authed_client.get("/api/settings")
    data = resp.json()
    assert "ghost_admin_api_key" not in data
    assert "ghost_admin_api_key_enc" not in data


@pytest.mark.asyncio
async def test_update_schedule_valid(authed_client):
    """PUT /api/settings/schedule with valid data succeeds."""
    resp = await authed_client.put("/api/settings/schedule", json={
        "publish_days": '["monday","wednesday"]',
        "publish_time": "10:00",
        "publish_timezone": "America/New_York",
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_update_schedule_too_many_days(authed_client):
    """Reject schedule with more than 3 days."""
    resp = await authed_client.put("/api/settings/schedule", json={
        "publish_days": '["monday","tuesday","wednesday","thursday"]',
        "publish_time": "10:00",
        "publish_timezone": "America/New_York",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_schedule_invalid_timezone(authed_client):
    """Reject invalid timezone."""
    resp = await authed_client.put("/api/settings/schedule", json={
        "publish_days": '["monday"]',
        "publish_time": "10:00",
        "publish_timezone": "Invalid/Timezone",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_schedule_invalid_time(authed_client):
    """Reject invalid time format."""
    resp = await authed_client.put("/api/settings/schedule", json={
        "publish_days": '["monday"]',
        "publish_time": "25:00",
        "publish_timezone": "America/New_York",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_profile(authed_client):
    """PUT /api/settings/profile updates brand voice and word count."""
    resp = await authed_client.put("/api/settings/profile", json={
        "brand_voice": "Casual and fun",
        "default_word_count": 2000,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["brand_voice"] == "Casual and fun"
    assert data["default_word_count"] == 2000


@pytest.mark.asyncio
async def test_update_profile_image_style(authed_client):
    """PUT /api/settings/profile updates validated image style pair."""
    resp = await authed_client.put("/api/settings/profile", json={
        "image_style": "illustration",
        "image_substyle": "isometric",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["image_style"] == "illustration"
    assert data["image_substyle"] == "isometric"

    settings = await authed_client.get("/api/settings")
    assert settings.json()["image_style"] == "illustration"
    assert settings.json()["image_substyle"] == "isometric"


@pytest.mark.asyncio
async def test_update_profile_rejects_invalid_image_substyle(authed_client):
    """Sub-style must belong to the selected primary style."""
    resp = await authed_client.put("/api/settings/profile", json={
        "image_style": "photography",
        "image_substyle": "isometric",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unauthenticated_settings(app, config):
    """Unauthenticated request to settings → 401."""
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/settings")
        assert resp.status_code == 401
