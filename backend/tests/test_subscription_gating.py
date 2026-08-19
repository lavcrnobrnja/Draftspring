"""Tests for subscription gating on all write endpoints."""

import pytest
import pytest_asyncio

from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.middleware.auth_middleware import create_session
from app.middleware.subscription import require_active_subscription, ACTIVE_STATUSES


@pytest.fixture
def app(config):
    return create_app(config)


async def _make_user(config, email, sub_status="none"):
    """Create a user with given subscription status, return (user_id, session_id)."""
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
        user = await create_user(db, email)
        await update_user(db, user["id"], subscription_status=sub_status, ghost_key_valid=1)
        session_id = await create_session(db, user["id"], "full")
    return user["id"], session_id


@pytest_asyncio.fixture
async def no_sub_client(app, config):
    """Authenticated client with NO subscription."""
    _, session_id = await _make_user(config, "nosub@test.com", "none")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        yield ac


@pytest_asyncio.fixture
async def active_client(app, config):
    """Authenticated client with ACTIVE subscription."""
    _, session_id = await _make_user(config, "active@test.com", "active")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        yield ac


@pytest_asyncio.fixture
async def trialing_client(app, config):
    """Authenticated client with TRIALING subscription."""
    _, session_id = await _make_user(config, "trial@test.com", "trialing")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        yield ac


@pytest_asyncio.fixture
async def canceled_client(app, config):
    """Authenticated client with CANCELED subscription."""
    _, session_id = await _make_user(config, "canceled@test.com", "canceled")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        yield ac


@pytest_asyncio.fixture
async def past_due_client(app, config):
    """Authenticated client with PAST_DUE subscription."""
    _, session_id = await _make_user(config, "pastdue@test.com", "past_due")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        yield ac


class TestSubscriptionGatingUnit:
    """Unit tests for the require_active_subscription helper."""

    def test_active_passes(self):
        require_active_subscription({"subscription_status": "active"})

    def test_trialing_passes(self):
        require_active_subscription({"subscription_status": "trialing"})

    def test_none_raises_403(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            require_active_subscription({"subscription_status": "none"})
        assert exc.value.status_code == 403
        assert exc.value.detail["error"] == "subscription_required"

    def test_canceled_raises_403(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            require_active_subscription({"subscription_status": "canceled"})
        assert exc.value.status_code == 403

    def test_past_due_raises_403(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            require_active_subscription({"subscription_status": "past_due"})
        assert exc.value.status_code == 403

    def test_missing_status_raises_403(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            require_active_subscription({})
        assert exc.value.status_code == 403

    def test_active_statuses_constant(self):
        assert ACTIVE_STATUSES == {"active", "trialing"}


class TestSeedCreationGating:
    """POST /api/seeds is gated by subscription."""

    VALID_BODY = {"seeds": [{"type": "topic", "content": "Test topic"}]}

    @pytest.mark.asyncio
    async def test_no_subscription_blocked(self, no_sub_client):
        resp = await no_sub_client.post("/api/seeds", json=self.VALID_BODY)
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "subscription_required"

    @pytest.mark.asyncio
    async def test_canceled_blocked(self, canceled_client):
        resp = await canceled_client.post("/api/seeds", json=self.VALID_BODY)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_past_due_blocked(self, past_due_client):
        resp = await past_due_client.post("/api/seeds", json=self.VALID_BODY)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_active_allowed(self, active_client):
        """Active subscription can create seeds (may fail on other validation, not 403)."""
        resp = await active_client.post("/api/seeds", json=self.VALID_BODY)
        assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_trialing_allowed(self, trialing_client):
        """Trialing subscription can create seeds."""
        resp = await trialing_client.post("/api/seeds", json=self.VALID_BODY)
        assert resp.status_code != 403


class TestSeedImageUploadGating:
    """POST /api/seeds/{id}/images is gated by subscription."""

    @pytest.mark.asyncio
    async def test_no_subscription_blocked(self, no_sub_client):
        resp = await no_sub_client.post(
            "/api/seeds/fake_id/images",
            files={"file": ("test.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_active_not_403(self, active_client):
        """Active user doesn't get 403 (may get 404 for fake seed)."""
        resp = await active_client.post(
            "/api/seeds/fake_id/images",
            files={"file": ("test.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert resp.status_code != 403


class TestCheckpoint1Gating:
    """POST /api/checkpoints/ideas/approve is gated by subscription."""

    VALID_BODY = {"batch_id": "fake_batch", "approved_ideas": []}

    @pytest.mark.asyncio
    async def test_no_subscription_blocked(self, no_sub_client):
        resp = await no_sub_client.post("/api/checkpoints/ideas/approve", json=self.VALID_BODY)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_active_not_403(self, active_client):
        resp = await active_client.post("/api/checkpoints/ideas/approve", json=self.VALID_BODY)
        assert resp.status_code != 403  # Will be 404 for fake batch, not 403


class TestCheckpoint2Gating:
    """POST /api/checkpoints/article/approve and /revise are gated."""

    @pytest.mark.asyncio
    async def test_approve_no_subscription_blocked(self, no_sub_client):
        resp = await no_sub_client.post(
            "/api/checkpoints/article/approve",
            json={"article_id": "fake_article"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_revise_no_subscription_blocked(self, no_sub_client):
        resp = await no_sub_client.post(
            "/api/checkpoints/article/revise",
            json={"article_id": "fake_article", "revision_notes": "Please fix the introduction paragraph and tone"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_approve_active_not_403(self, active_client):
        resp = await active_client.post(
            "/api/checkpoints/article/approve",
            json={"article_id": "fake_article"},
        )
        assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_revise_active_not_403(self, active_client):
        resp = await active_client.post(
            "/api/checkpoints/article/revise",
            json={"article_id": "fake_article", "revision_notes": "Please fix the introduction paragraph and tone"},
        )
        assert resp.status_code != 403


class TestReadEndpointsNotGated:
    """Read-only endpoints should NOT be gated by subscription."""

    @pytest.mark.asyncio
    async def test_dashboard_accessible_without_subscription(self, no_sub_client):
        """GET /api/articles should not return 403."""
        resp = await no_sub_client.get("/api/articles")
        assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_settings_accessible_without_subscription(self, no_sub_client):
        """GET /api/settings should not return 403."""
        resp = await no_sub_client.get("/api/settings")
        assert resp.status_code != 403

    @pytest.mark.asyncio
    async def test_vault_gallery_accessible_without_subscription(self, no_sub_client):
        """GET /api/vault/gallery should not return 403."""
        resp = await no_sub_client.get("/api/vault/gallery")
        assert resp.status_code != 403
