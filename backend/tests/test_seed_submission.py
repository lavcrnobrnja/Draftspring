"""Tests for seed submission route (Task 2.3)."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.config import Config
from app.database import get_connection, run_migrations
from app.main import create_app
from app.models.user import create_user, update_user
from app.middleware.auth_middleware import create_session
from app.utils.ulid import generate_id

from tests.conftest import *


@pytest_asyncio.fixture
async def active_user(db):
    """User with active subscription and valid Ghost connection."""
    user = await create_user(db, "seeds@test.com")
    await update_user(
        db, user["id"],
        subscription_status="active",
        ghost_key_valid=1,
        ghost_url="https://blog.example.com",
    )
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
    return dict(await cursor.fetchone())


@pytest_asyncio.fixture
async def auth_session(db, active_user):
    """Active session for authenticated requests."""
    session_id = await create_session(db, active_user["id"], "full")
    return session_id


@pytest.fixture
def client(config, db):
    """TestClient with shared db path."""
    app = create_app(config)
    return TestClient(app)


class TestSeedSubmission:
    @pytest.mark.asyncio
    async def test_submit_valid_seeds(self, db, config, active_user, auth_session):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={
                "seeds": [
                    {"seed_type": "topic", "content": "AI in healthcare"},
                    {"seed_type": "topic", "content": "Python testing best practices"},
                ]
            },
            cookies={"session_id": auth_session},
        )
        assert response.status_code == 201
        data = response.json()
        assert "batch_id" in data
        assert data["seed_count"] == 2

    @pytest.mark.asyncio
    async def test_reject_empty_seeds(self, db, config, active_user, auth_session):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={"seeds": []},
            cookies={"session_id": auth_session},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_reject_too_many_seeds(self, db, config, active_user, auth_session):
        app = create_app(config)
        client = TestClient(app)
        seeds = [{"seed_type": "topic", "content": f"Topic {i}"} for i in range(11)]
        response = client.post(
            "/api/seeds",
            json={"seeds": seeds},
            cookies={"session_id": auth_session},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_reject_invalid_type(self, db, config, active_user, auth_session):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={"seeds": [{"seed_type": "video", "content": "something"}]},
            cookies={"session_id": auth_session},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_reject_invalid_url(self, db, config, active_user, auth_session):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={"seeds": [{"seed_type": "url", "content": "not-a-url"}]},
            cookies={"session_id": auth_session},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_accept_valid_url(self, db, config, active_user, auth_session):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={"seeds": [{"seed_type": "url", "content": "https://example.com/article"}]},
            cookies={"session_id": auth_session},
        )
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_content_brief_accepts_image_style_override(self, db, config, active_user, auth_session):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={
                "description": "Write about onboarding metrics",
                "image_style": "illustration",
                "image_substyle": "isometric",
            },
            cookies={"session_id": auth_session},
        )
        assert response.status_code == 201
        batch_id = response.json()["batch_id"]
        cursor = await db.execute("SELECT image_style, image_substyle FROM seed_batches WHERE id = ?", (batch_id,))
        row = await cursor.fetchone()
        assert row["image_style"] == "illustration"
        assert row["image_substyle"] == "isometric"

    @pytest.mark.asyncio
    async def test_content_brief_omits_image_style_when_using_profile_default(self, db, config, active_user, auth_session):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={"description": "Write about onboarding metrics"},
            cookies={"session_id": auth_session},
        )
        assert response.status_code == 201
        batch_id = response.json()["batch_id"]
        cursor = await db.execute("SELECT image_style, image_substyle FROM seed_batches WHERE id = ?", (batch_id,))
        row = await cursor.fetchone()
        assert row["image_style"] is None
        assert row["image_substyle"] is None

    @pytest.mark.asyncio
    async def test_content_brief_rejects_invalid_image_style_pair(self, db, config, active_user, auth_session):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={
                "description": "Write about onboarding metrics",
                "image_style": "photography",
                "image_substyle": "isometric",
            },
            cookies={"session_id": auth_session},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_reject_no_auth(self, db, config):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={"seeds": [{"seed_type": "topic", "content": "Test"}]},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_reject_canceled_subscription(self, db, config):
        user = await create_user(db, "canceled@test.com")
        await update_user(db, user["id"], subscription_status="canceled", ghost_key_valid=1)
        session_id = await create_session(db, user["id"], "full")
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={"seeds": [{"seed_type": "topic", "content": "Test"}]},
            cookies={"session_id": session_id},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_reject_no_ghost_connection(self, db, config):
        user = await create_user(db, "noghost@test.com")
        await update_user(db, user["id"], subscription_status="active", ghost_key_valid=0)
        session_id = await create_session(db, user["id"], "full")
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/seeds",
            json={"seeds": [{"seed_type": "topic", "content": "Test"}]},
            cookies={"session_id": session_id},
        )
        assert response.status_code == 400
