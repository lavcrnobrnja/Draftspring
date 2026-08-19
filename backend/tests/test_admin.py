"""Phase 5.1 + 5.2: Admin routes tests."""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.config import Config
from app.database import get_connection, run_migrations
from app.models.user import create_user
from app.middleware.auth_middleware import create_session
from app.services.email import clear_sent_emails, get_sent_emails


@pytest.fixture
def admin_config(config):
    """Config with admin emails set."""
    config.ADMIN_EMAILS = "admin@test.com,boss@test.com"
    return config


@pytest.fixture
def app(admin_config):
    return create_app(admin_config)


@pytest_asyncio.fixture
async def admin_client(app, admin_config):
    """Client authenticated as an admin user."""
    async with get_connection(admin_config.DATABASE_PATH) as db:
        await run_migrations(db)
        user = await create_user(db, "admin@test.com")
        session_id = await create_session(db, user["id"], "full")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        clear_sent_emails()
        yield ac


@pytest_asyncio.fixture
async def non_admin_client(app, admin_config):
    """Client authenticated as a non-admin user."""
    async with get_connection(admin_config.DATABASE_PATH) as db:
        await run_migrations(db)
        user = await create_user(db, "normie@test.com")
        session_id = await create_session(db, user["id"], "full")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("session_id", session_id)
        yield ac


@pytest_asyncio.fixture
async def unauthed_client(app):
    """Client with no session."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Helper: seed test data ──


async def _seed_articles(config, user_email="admin@test.com", count=3, state="OUTLINING"):
    """Create articles for testing. Returns list of article ids."""
    from app.utils.ulid import generate_id
    from app.utils.time import utc_now

    article_ids = []
    async with get_connection(config.DATABASE_PATH) as db:
        cursor = await db.execute("SELECT id FROM users WHERE email = ?", (user_email,))
        user = await cursor.fetchone()
        if not user:
            # Create the user if they don't exist
            user = await create_user(db, user_email)
        user_id = user["id"]

        now = utc_now()
        for i in range(count):
            batch_id = generate_id()
            seed_id = generate_id()
            idea_id = generate_id()
            article_id = generate_id()

            await db.execute(
                "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'processed', ?)",
                (batch_id, user_id, now),
            )
            await db.execute(
                "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', ?, ?)",
                (seed_id, batch_id, f"Test seed {i}", now),
            )
            await db.execute(
                "INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'approved', ?)",
                (idea_id, batch_id, seed_id, f"Test Article {i}", f"Angle {i}", f"keyword-{i}", now),
            )
            await db.execute(
                """INSERT INTO articles (id, user_id, idea_id, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (article_id, user_id, idea_id, state, now, now),
            )
            article_ids.append(article_id)

        await db.commit()
    return article_ids


# ═══════════════════════════════════════════════════════════
# Task 5.1: Admin Auth + Tables
# ═══════════════════════════════════════════════════════════


class TestAdminAuth:
    """Non-admin and unauthenticated users get rejected."""

    @pytest.mark.asyncio
    async def test_non_admin_overview_returns_403(self, non_admin_client):
        resp = await non_admin_client.get("/api/admin/overview")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_non_admin_users_returns_403(self, non_admin_client):
        resp = await non_admin_client.get("/api/admin/users")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_non_admin_articles_returns_403(self, non_admin_client):
        resp = await non_admin_client.get("/api/admin/articles")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_unauthenticated_overview_returns_401(self, unauthed_client):
        resp = await unauthed_client.get("/api/admin/overview")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_unauthenticated_users_returns_401(self, unauthed_client):
        resp = await unauthed_client.get("/api/admin/users")
        assert resp.status_code == 401


class TestAdminOverview:
    """GET /api/admin/overview returns stat summary."""

    @pytest.mark.asyncio
    async def test_overview_returns_stats(self, admin_client, admin_config):
        resp = await admin_client.get("/api/admin/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_users" in data
        assert "total_articles" in data
        assert "articles_by_state" in data
        assert "active_subscriptions" in data
        assert "estimated_mrr_cents" in data
        assert "failed_count" in data

    @pytest.mark.asyncio
    async def test_overview_counts_articles(self, admin_client, admin_config):
        await _seed_articles(admin_config, count=2, state="OUTLINING")
        await _seed_articles(admin_config, count=1, state="FAILED")
        resp = await admin_client.get("/api/admin/overview")
        data = resp.json()
        assert data["total_articles"] == 3
        assert data["articles_by_state"].get("OUTLINING") == 2
        assert data["failed_count"] == 1


class TestAdminUsers:
    """GET /api/admin/users — paginated, searchable, filterable."""

    @pytest.mark.asyncio
    async def test_users_returns_paginated(self, admin_client):
        resp = await admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert "total" in data
        assert "page" in data
        assert "pages" in data
        # At least admin user exists
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_users_searchable_by_email(self, admin_client):
        resp = await admin_client.get("/api/admin/users?search=admin")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for u in data["users"]:
            assert "admin" in u["email"]

    @pytest.mark.asyncio
    async def test_users_search_no_results(self, admin_client):
        resp = await admin_client.get("/api/admin/users?search=nonexistent-xyz")
        data = resp.json()
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_users_filterable_by_status(self, admin_client):
        resp = await admin_client.get("/api/admin/users?status=active")
        data = resp.json()
        for u in data["users"]:
            assert u["subscription_status"] == "active"

    @pytest.mark.asyncio
    async def test_users_include_article_counts(self, admin_client, admin_config):
        await _seed_articles(admin_config, count=2)
        resp = await admin_client.get("/api/admin/users")
        data = resp.json()
        admin_user = next(u for u in data["users"] if u["email"] == "admin@test.com")
        assert admin_user["article_count"] == 2


class TestAdminArticles:
    """GET /api/admin/articles — paginated, filterable by state/user."""

    @pytest.mark.asyncio
    async def test_articles_returns_paginated(self, admin_client, admin_config):
        await _seed_articles(admin_config, count=3)
        resp = await admin_client.get("/api/admin/articles")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["articles"]) == 3
        assert "user_email" in data["articles"][0]

    @pytest.mark.asyncio
    async def test_articles_filterable_by_state(self, admin_client, admin_config):
        await _seed_articles(admin_config, count=2, state="OUTLINING")
        await _seed_articles(admin_config, count=1, state="FAILED")
        resp = await admin_client.get("/api/admin/articles?state=FAILED")
        data = resp.json()
        assert data["total"] == 1
        assert data["articles"][0]["state"] == "FAILED"

    @pytest.mark.asyncio
    async def test_articles_filterable_by_user(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=2)
        # Get the user ID
        async with get_connection(admin_config.DATABASE_PATH) as db:
            cursor = await db.execute("SELECT id FROM users WHERE email = 'admin@test.com'")
            user = await cursor.fetchone()
        resp = await admin_client.get(f"/api/admin/articles?user_id={user['id']}")
        data = resp.json()
        assert data["total"] == 2

    @pytest.mark.asyncio
    async def test_articles_pagination(self, admin_client, admin_config):
        await _seed_articles(admin_config, count=5)
        resp = await admin_client.get("/api/admin/articles?per_page=2&page=1")
        data = resp.json()
        assert len(data["articles"]) == 2
        assert data["total"] == 5
        assert data["pages"] == 3


class TestAdminArticleDetail:
    """GET /api/admin/articles/{id} — full detail with events."""

    @pytest.mark.asyncio
    async def test_article_detail_returns_full_data(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1)
        resp = await admin_client.get(f"/api/admin/articles/{article_ids[0]}")
        assert resp.status_code == 200
        data = resp.json()["article"]
        assert data["id"] == article_ids[0]
        assert "title" in data
        assert "pipeline_events" in data
        assert "draft_iterations" in data
        assert "images" in data
        assert "reviews" in data

    @pytest.mark.asyncio
    async def test_article_detail_not_found(self, admin_client):
        resp = await admin_client.get("/api/admin/articles/nonexistent-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_non_admin_article_detail_403(self, non_admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1)
        resp = await non_admin_client.get(f"/api/admin/articles/{article_ids[0]}")
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════
# Task 5.2: Admin Actions
# ═══════════════════════════════════════════════════════════


class TestAdminRetry:
    """POST /api/admin/articles/{id}/retry — reset FAILED article."""

    @pytest.mark.asyncio
    async def test_retry_failed_article(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="FAILED")
        # Set a failure reason
        async with get_connection(admin_config.DATABASE_PATH) as db:
            await db.execute(
                "UPDATE articles SET failure_reason = 'LLM timeout', failed_at = '2026-01-01T00:00:00Z' WHERE id = ?",
                (article_ids[0],),
            )
            await db.commit()

        resp = await admin_client.post(f"/api/admin/articles/{article_ids[0]}/retry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_state"] != "FAILED"
        assert data["failure_cleared"] is True

    @pytest.mark.asyncio
    async def test_retry_non_failed_article_rejects(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="OUTLINING")
        resp = await admin_client.post(f"/api/admin/articles/{article_ids[0]}/retry")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_retry_not_found(self, admin_client):
        resp = await admin_client.post("/api/admin/articles/nonexistent/retry")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_non_admin_retry_403(self, non_admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="FAILED")
        resp = await non_admin_client.post(f"/api/admin/articles/{article_ids[0]}/retry")
        assert resp.status_code == 403


class TestAdminRollback:
    """POST /api/admin/articles/{id}/rollback — set to any valid state."""

    @pytest.mark.asyncio
    async def test_rollback_to_prior_state(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="MEDIA_ASSEMBLY")
        resp = await admin_client.post(
            f"/api/admin/articles/{article_ids[0]}/rollback",
            json={"target_state": "DRAFTING"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["new_state"] == "DRAFTING"

    @pytest.mark.asyncio
    async def test_rollback_with_iteration_reset(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="EDIT_REVIEW")
        # Set iteration count
        async with get_connection(admin_config.DATABASE_PATH) as db:
            await db.execute(
                "UPDATE articles SET lifetime_draft_iterations = 3 WHERE id = ?",
                (article_ids[0],),
            )
            await db.commit()

        resp = await admin_client.post(
            f"/api/admin/articles/{article_ids[0]}/rollback",
            json={"target_state": "DRAFTING", "reset_iterations": True},
        )
        assert resp.status_code == 200
        # Verify iteration was reset
        async with get_connection(admin_config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT lifetime_draft_iterations FROM articles WHERE id = ?",
                (article_ids[0],),
            )
            article = await cursor.fetchone()
            assert article["lifetime_draft_iterations"] == 0

    @pytest.mark.asyncio
    async def test_rollback_invalid_state(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="OUTLINING")
        resp = await admin_client.post(
            f"/api/admin/articles/{article_ids[0]}/rollback",
            json={"target_state": "INVALID_STATE"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_rollback_not_found(self, admin_client):
        resp = await admin_client.post(
            "/api/admin/articles/nonexistent/rollback",
            json={"target_state": "DRAFTING"},
        )
        assert resp.status_code == 404


class TestAdminArchive:
    """POST /api/admin/articles/{id}/archive — set ARCHIVED, send email."""

    @pytest.mark.asyncio
    async def test_archive_article(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="OUTLINING")
        clear_sent_emails()
        resp = await admin_client.post(f"/api/admin/articles/{article_ids[0]}/archive")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "ARCHIVED"

    @pytest.mark.asyncio
    async def test_archive_sends_notification(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="OUTLINING")
        clear_sent_emails()
        resp = await admin_client.post(f"/api/admin/articles/{article_ids[0]}/archive")
        assert resp.status_code == 200
        emails = get_sent_emails()
        assert len(emails) >= 1
        assert "archived" in emails[-1]["subject"].lower() or "archive" in emails[-1]["html"].lower()

    @pytest.mark.asyncio
    async def test_archive_already_archived(self, admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="ARCHIVED")
        resp = await admin_client.post(f"/api/admin/articles/{article_ids[0]}/archive")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_non_admin_archive_403(self, non_admin_client, admin_config):
        article_ids = await _seed_articles(admin_config, count=1, state="OUTLINING")
        resp = await non_admin_client.post(f"/api/admin/articles/{article_ids[0]}/archive")
        assert resp.status_code == 403
