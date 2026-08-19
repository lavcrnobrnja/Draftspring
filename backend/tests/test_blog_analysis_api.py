"""Tests for blog analysis API endpoints."""

import sqlite3
import json
import os
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch

from app.config import Config
from app.database import get_connection, run_migrations
from app.main import create_app
from app.middleware.auth_middleware import create_session
from app.services.blog_analyzer import BlogProfile, ArticleIdea, BlogAnalyzerError
from app.utils.ulid import generate_id
from app.utils.time import utc_now


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def config():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    cfg = Config(
        APP_ENV="test",
        DATABASE_PATH=db_path,
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        STRIPE_WEBHOOK_SECRET="whsec_test",
    )
    yield cfg
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


@pytest.fixture
def app(config):
    return create_app(config)


async def _create_user(db, email="test@example.com", subscription_status="active"):
    user_id = generate_id()
    now = utc_now()
    await db.execute(
        """INSERT INTO users (id, email, subscription_status, ghost_key_valid,
           publish_days, publish_time, publish_timezone, articles_per_cycle_limit,
           brand_voice, default_word_count, created_at, updated_at)
           VALUES (?, ?, ?, 1, '["monday"]', '09:00', 'America/New_York', 10, '', 1500, ?, ?)""",
        (user_id, email, subscription_status, now, now),
    )
    await db.commit()
    return user_id


@pytest_asyncio.fixture
async def client(app, config):
    """Unauthenticated client."""
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(app, config):
    """Authenticated client with active subscription."""
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
        user_id = await _create_user(db, subscription_status="active")
        session_id = await create_session(db, user_id, scope="full")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"session_id": session_id},
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_client_no_subscription(app, config):
    """Authenticated client WITHOUT subscription."""
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
        user_id = await _create_user(db, email="nosub@example.com", subscription_status="none")
        session_id = await create_session(db, user_id, scope="full")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"session_id": session_id},
    ) as c:
        yield c


async def _create_user_no_ghost(db, email="noghost@example.com"):
    user_id = generate_id()
    now = utc_now()
    await db.execute(
        """INSERT INTO users (id, email, subscription_status, ghost_key_valid,
           publish_days, publish_time, publish_timezone, articles_per_cycle_limit,
           brand_voice, default_word_count, created_at, updated_at)
           VALUES (?, ?, 'active', 0, '["monday"]', '09:00', 'America/New_York', 10, '', 1500, ?, ?)""",
        (user_id, email, now, now),
    )
    await db.commit()
    return user_id


@pytest_asyncio.fixture
async def auth_client_no_ghost(app, config):
    """Authenticated client with active subscription but NO Ghost connected."""
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
        user_id = await _create_user_no_ghost(db)
        session_id = await create_session(db, user_id, scope="full")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"session_id": session_id},
    ) as c:
        yield c


def _make_profile(**overrides):
    defaults = dict(
        id="test-profile-id",
        url="https://example.com",
        site_name="Test Blog",
        is_ghost=True,
        topics=["python", "ai"],
        content_gaps=["devops", "testing"],
        style_guide="Conversational tone.",
        example_sentences=["Here's the thing."],
        audience_description="Python developers",
        tone_keywords=["conversational", "technical"],
        strengths=["clear examples", "practical tips"],
        avg_word_count=1200,
        total_posts=20,
        latest_post_date="2026-01-01T00:00:00",
        publishing_frequency="weekly",
        post_summaries=[{"title": "Post 1", "url": "/post-1", "date": "2026-01-01"}],
        analyzed_at="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return BlogProfile(**defaults)


# ── POST /api/blog-analysis/analyze ──────────────────────────────────

class TestAnalyzeEndpoint:

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        resp = await client.post(
            "/api/blog-analysis/analyze",
            json={"url": "https://example.com"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_requires_subscription(self, auth_client_no_subscription):
        resp = await auth_client_no_subscription.post(
            "/api/blog-analysis/analyze",
            json={"url": "https://example.com"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_url(self, auth_client):
        resp = await auth_client.post(
            "/api/blog-analysis/analyze",
            json={"url": "not-a-url"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_success(self, auth_client):
        profile = _make_profile()
        with patch("app.routes.blog_analysis.BlogAnalyzer") as MockAnalyzer:
            instance = MockAnalyzer.return_value
            instance.get_or_analyze = AsyncMock(return_value=profile)

            resp = await auth_client.post(
                "/api/blog-analysis/analyze",
                json={"url": "https://example.com"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "profile" in data
        assert data["profile"]["site_name"] == "Test Blog"
        assert data["profile"]["topics"] == ["python", "ai"]
        assert data["profile"]["audience_description"] == "Python developers"

    @pytest.mark.asyncio
    async def test_analyzer_error(self, auth_client):
        with patch("app.routes.blog_analysis.BlogAnalyzer") as MockAnalyzer:
            instance = MockAnalyzer.return_value
            instance.get_or_analyze = AsyncMock(
                side_effect=BlogAnalyzerError("No RSS found")
            )

            resp = await auth_client.post(
                "/api/blog-analysis/analyze",
                json={"url": "https://example.com"},
            )

        assert resp.status_code == 400
        assert "No RSS found" in resp.json()["detail"]


# ── POST /api/blog-analysis/generate-ideas ───────────────────────────

class TestGenerateIdeasEndpoint:

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        resp = await client.post(
            "/api/blog-analysis/generate-ideas",
            json={"profile_id": "abc", "count": 5},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_requires_subscription(self, auth_client_no_subscription):
        resp = await auth_client_no_subscription.post(
            "/api/blog-analysis/generate-ideas",
            json={"profile_id": "abc", "count": 5},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_profile_not_found(self, auth_client):
        resp = await auth_client.post(
            "/api/blog-analysis/generate-ideas",
            json={"profile_id": "nonexistent", "count": 5},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_count(self, auth_client):
        resp = await auth_client.post(
            "/api/blog-analysis/generate-ideas",
            json={"profile_id": "abc", "count": 0},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_success(self, auth_client, config):
        # Insert profile into DB
        async with get_connection(config.DATABASE_PATH) as db:
            await db.execute(
                """INSERT INTO blog_profiles (id, url, site_name, is_ghost, profile_data, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    "test-prof-1",
                    "https://example.com",
                    "Test Blog",
                    True,
                    json.dumps({
                        "topics": ["python"],
                        "content_gaps": ["devops"],
                        "style_guide": "Casual",
                        "example_sentences": [],
                        "audience_description": "developers",
                        "tone_keywords": ["casual"],
                        "strengths": ["examples"],
                        "avg_word_count": 1000,
                        "total_posts": 10,
                        "latest_post_date": "",
                        "publishing_frequency": "weekly",
                        "post_summaries": [],
                    }),
                    "2026-04-01T00:00:00+00:00",
                ),
            )
            await db.commit()

        mock_ideas = [
            ArticleIdea(title="Test Idea", angle="Fresh", article_type="how-to", reasoning="Fills gap"),
        ]

        with patch("app.routes.blog_analysis.BlogAnalyzer") as MockAnalyzer:
            instance = MockAnalyzer.return_value
            instance.generate_ideas = AsyncMock(return_value=mock_ideas)

            resp = await auth_client.post(
                "/api/blog-analysis/generate-ideas",
                json={"profile_id": "test-prof-1", "count": 5},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["ideas"]) == 1
        assert data["ideas"][0]["title"] == "Test Idea"


# ── POST /api/seeds/from-analysis ────────────────────────────────────

class TestFromAnalysisEndpoint:

    @pytest.mark.asyncio
    async def test_requires_auth(self, client):
        resp = await client.post(
            "/api/seeds/from-analysis",
            json={"profile_id": "abc", "ideas": [{"title": "T"}]},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_requires_subscription(self, auth_client_no_subscription):
        resp = await auth_client_no_subscription.post(
            "/api/seeds/from-analysis",
            json={"profile_id": "abc", "ideas": [{"title": "T"}]},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_ideas(self, auth_client):
        resp = await auth_client.post(
            "/api/seeds/from-analysis",
            json={"profile_id": "abc", "ideas": []},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_profile_not_found(self, auth_client):
        resp = await auth_client.post(
            "/api/seeds/from-analysis",
            json={"profile_id": "nonexistent", "ideas": [{"title": "T"}]},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_success(self, auth_client, config):
        # Insert profile
        async with get_connection(config.DATABASE_PATH) as db:
            await db.execute(
                """INSERT OR REPLACE INTO blog_profiles (id, url, site_name, is_ghost, profile_data, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("from-prof", "https://testblog.com", "Test", True,
                 json.dumps({"topics": []}), "2026-04-01T00:00:00+00:00"),
            )
            await db.commit()

        resp = await auth_client.post(
            "/api/seeds/from-analysis",
            json={
                "profile_id": "from-prof",
                "ideas": [
                    {"title": "Idea 1", "angle": "Fresh take", "article_type": "how-to"},
                    {"title": "Idea 2", "angle": "Deep dive", "article_type": "deep-dive"},
                ],
            },
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["seed_count"] == 2
        assert data["articles_created"] == 2
        assert data["budget_limited"] is False
        assert "batch_id" in data

        async with get_connection(config.DATABASE_PATH) as db:
            # Batch: source='analysis' AND status='processed'
            rows = await db.execute_fetchall(
                "SELECT source, status FROM seed_batches WHERE id = ?",
                (data["batch_id"],),
            )
            assert rows[0]["source"] == "analysis"
            assert rows[0]["status"] == "processed"

            # Seeds created with profile URL context (one per idea)
            seeds = await db.execute_fetchall(
                "SELECT content FROM seeds WHERE batch_id = ? ORDER BY created_at",
                (data["batch_id"],),
            )
            assert len(seeds) == 2
            assert "Idea 1" in seeds[0]["content"]
            assert "testblog.com" in seeds[0]["content"]

            # Ideas persisted with status='approved' (post approve_ideas)
            ideas = await db.execute_fetchall(
                "SELECT id, title, status FROM ideas WHERE batch_id = ? ORDER BY created_at",
                (data["batch_id"],),
            )
            assert len(ideas) == 2
            assert all(i["status"] == "approved" for i in ideas)
            titles = {i["title"] for i in ideas}
            assert titles == {"Idea 1", "Idea 2"}

            # Articles exist in OUTLINING state, linked via idea.batch_id
            articles = await db.execute_fetchall(
                """SELECT a.id, a.state FROM articles a
                   JOIN ideas i ON a.idea_id = i.id
                   WHERE i.batch_id = ?""",
                (data["batch_id"],),
            )
            assert len(articles) == 2
            assert all(a["state"] == "OUTLINING" for a in articles)

    @pytest.mark.asyncio
    async def test_retries_transient_sqlite_locked_error(self, auth_client, config):
        async with get_connection(config.DATABASE_PATH) as db:
            await db.execute(
                """INSERT OR REPLACE INTO blog_profiles (id, url, site_name, is_ghost, profile_data, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("retry-prof", "https://retryblog.com", "Retry", True,
                 json.dumps({"topics": []}), "2026-04-01T00:00:00+00:00"),
            )
            await db.commit()

        from app.routes import blog_analysis as blog_routes

        real_approve_ideas = blog_routes.approve_ideas
        attempts = 0

        async def flaky_approve_ideas(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise sqlite3.OperationalError("database is locked")
            return await real_approve_ideas(*args, **kwargs)

        with (
            patch.object(blog_routes, "approve_ideas", side_effect=flaky_approve_ideas),
            patch.object(blog_routes, "_SQLITE_LOCK_RETRY_DELAYS", (0,)),
        ):
            resp = await auth_client.post(
                "/api/seeds/from-analysis",
                json={"profile_id": "retry-prof", "ideas": [{"title": "Retry Idea"}]},
            )

        assert resp.status_code == 201
        assert attempts == 2
        assert resp.json()["articles_created"] == 1

    @pytest.mark.asyncio
    async def test_sqlite_locked_retries_exhaust_cleanly(self, auth_client, config):
        async with get_connection(config.DATABASE_PATH) as db:
            await db.execute(
                """INSERT OR REPLACE INTO blog_profiles (id, url, site_name, is_ghost, profile_data, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("locked-prof", "https://lockedblog.com", "Locked", True,
                 json.dumps({"topics": []}), "2026-04-01T00:00:00+00:00"),
            )
            await db.commit()

        from app.routes import blog_analysis as blog_routes

        async def locked_approve_ideas(*args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

        with (
            patch.object(blog_routes, "approve_ideas", side_effect=locked_approve_ideas),
            patch.object(blog_routes, "_SQLITE_LOCK_RETRY_DELAYS", (0,)),
        ):
            resp = await auth_client.post(
                "/api/seeds/from-analysis",
                json={"profile_id": "locked-prof", "ideas": [{"title": "Locked Idea"}]},
            )

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Database is busy. Please try again in a moment."

    @pytest.mark.asyncio
    async def test_non_lock_operational_error_is_not_retried(self):
        from app.routes import blog_analysis as blog_routes

        attempts = 0

        async def broken_operation():
            nonlocal attempts
            attempts += 1
            raise sqlite3.OperationalError("no such table: ideas")

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            await blog_routes._retry_sqlite_locked(broken_operation)

        assert attempts == 1

    @pytest.mark.asyncio
    async def test_requires_ghost_connection(self, auth_client_no_ghost):
        resp = await auth_client_no_ghost.post(
            "/api/seeds/from-analysis",
            json={"profile_id": "abc", "ideas": [{"title": "T"}]},
        )
        assert resp.status_code == 400
        assert "Ghost connection required" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_too_many_ideas(self, auth_client):
        resp = await auth_client.post(
            "/api/seeds/from-analysis",
            json={
                "profile_id": "abc",
                "ideas": [{"title": f"Idea {i}"} for i in range(4)],
            },
        )
        assert resp.status_code == 400
        assert "Maximum 3" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_min_one_idea(self, auth_client):
        # Zero ideas -> 400 ("At least one idea is required.")
        resp = await auth_client.post(
            "/api/seeds/from-analysis",
            json={"profile_id": "abc", "ideas": []},
        )
        assert resp.status_code == 400
        assert "At least one idea" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_budget_limited(self, app, config):
        # User with articles_per_cycle_limit=1, active subscription, Ghost connected
        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = generate_id()
            now = utc_now()
            await db.execute(
                """INSERT INTO users (id, email, subscription_status, ghost_key_valid,
                   publish_days, publish_time, publish_timezone, articles_per_cycle_limit,
                   brand_voice, default_word_count, created_at, updated_at)
                   VALUES (?, 'budget@example.com', 'active', 1,
                           '["monday"]', '09:00', 'America/New_York', 1, '', 1500, ?, ?)""",
                (user_id, now, now),
            )
            await db.commit()
            session_id = await create_session(db, user_id, scope="full")
            await db.execute(
                """INSERT OR REPLACE INTO blog_profiles (id, url, site_name, is_ghost, profile_data, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                ("budget-prof", "https://budget.com", "B", True,
                 json.dumps({"topics": []}), "2026-04-01T00:00:00+00:00"),
            )
            await db.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"session_id": session_id},
        ) as client:
            resp = await client.post(
                "/api/seeds/from-analysis",
                json={
                    "profile_id": "budget-prof",
                    "ideas": [
                        {"title": "First", "angle": "a1"},
                        {"title": "Second", "angle": "a2"},
                    ],
                },
            )

        assert resp.status_code == 409
        assert "1 article remaining" in resp.json()["detail"]

        async with get_connection(config.DATABASE_PATH) as db:
            rows = await db.execute_fetchall(
                "SELECT COUNT(*) AS c FROM seed_batches WHERE user_id = ? AND source = 'analysis'",
                (user_id,),
            )
            assert rows[0]["c"] == 0
