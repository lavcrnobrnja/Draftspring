"""Dashboard API tests: articles list, batches, cancel, state mapping,
seeds, checkpoints, settings, usage, auth enforcement, error handling."""

import json

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.middleware.auth_middleware import create_session
from app.utils.ulid import generate_id
from app.utils.time import utc_now


# ── Helpers ──

async def create_test_user(db, email="test@example.com", **overrides):
    """Create a test user and return their id."""
    user_id = generate_id()
    now = utc_now()
    defaults = dict(
        subscription_status="active",
        ghost_key_valid=1,
        publish_days='["monday"]',
        publish_time="09:00",
        publish_timezone="America/New_York",
        articles_per_cycle_limit=10,
        brand_voice="",
        default_word_count=1500,
    )
    defaults.update(overrides)
    cols = "id, email, created_at, updated_at, " + ", ".join(defaults.keys())
    placeholders = "?, ?, ?, ?, " + ", ".join("?" for _ in defaults)
    await db.execute(
        f"INSERT INTO users ({cols}) VALUES ({placeholders})",
        (user_id, email, now, now, *defaults.values()),
    )
    await db.commit()
    return user_id


async def create_test_article(db, user_id, state="DRAFTING", **kwargs):
    """Create a test article with associated idea (and seed/batch)."""
    idea_id = generate_id()
    seed_id = generate_id()
    article_id = kwargs.pop("article_id", generate_id())
    now = utc_now()

    batch_id = kwargs.pop("batch_id", generate_id())
    await db.execute(
        """INSERT OR IGNORE INTO seed_batches (id, user_id, status, created_at)
           VALUES (?, ?, 'processed', ?)""",
        (batch_id, user_id, now),
    )
    await db.execute(
        """INSERT OR IGNORE INTO seeds (id, batch_id, seed_type, content, created_at)
           VALUES (?, ?, 'topic', 'test topic', ?)""",
        (seed_id, batch_id, now),
    )
    await db.execute(
        """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'approved', ?)""",
        (idea_id, batch_id, seed_id,
         kwargs.get("title", "Test Article"),
         "Test angle",
         kwargs.get("keyword", "test keyword"),
         now),
    )

    seo_meta = kwargs.get("seo_meta", None)
    ghost_post_url = kwargs.get("ghost_post_url", None)
    locked_by = kwargs.get("locked_by", None)

    await db.execute(
        """INSERT INTO articles (id, idea_id, user_id, state, seo_meta, ghost_post_url,
           locked_by, lifetime_draft_iterations, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (article_id, idea_id, user_id, state, seo_meta, ghost_post_url, locked_by, now, now),
    )
    await db.commit()
    return article_id


async def create_test_batch(db, user_id, status="pending_ideation", seed_count=3):
    """Create a test seed batch with seeds."""
    batch_id = generate_id()
    now = utc_now()
    await db.execute(
        "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, ?, ?)",
        (batch_id, user_id, status, now),
    )
    for _ in range(seed_count):
        seed_id = generate_id()
        await db.execute(
            "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', 'test', ?)",
            (seed_id, batch_id, now),
        )
    await db.commit()
    return batch_id


async def create_test_images(db, article_id, total=3, valid=2):
    """Create test article images (valid ones have real URLs, invalid have local://)."""
    now = utc_now()
    for i in range(total):
        img_id = generate_id()
        storage_url = f"https://cdn.example.com/img_{i}.jpg" if i < valid else "local://placeholder"
        await db.execute(
            """INSERT INTO article_images (id, article_id, source_type, storage_url, anchor_index, alt_text, created_at)
               VALUES (?, ?, 'generated', ?, ?, ?, ?)""",
            (img_id, article_id, storage_url, i, f"Image {i}", now),
        )
    await db.commit()


async def create_usage_ledger(db, user_id, articles_started=0):
    """Create a usage ledger entry for current cycle."""
    ledger_id = generate_id()
    now = utc_now()
    await db.execute(
        """INSERT INTO usage_ledger (id, user_id, billing_cycle_start, billing_cycle_end,
           articles_started, articles_published, updated_at)
           VALUES (?, ?, ?, '2030-01-01T00:00:00Z', ?, 0, ?)""",
        (ledger_id, user_id, now, articles_started, now),
    )
    await db.commit()


async def create_waiting_checkpoint2_article(db, user_id, **kwargs):
    """Create an article in WAITING_CHECKPOINT_2 state with a draft and pending review."""
    article_id = await create_test_article(
        db, user_id, state="WAITING_CHECKPOINT_2",
        seo_meta='{"focus_keyword":"test","meta_title":"Test"}',
        **kwargs,
    )
    now = utc_now()
    # Create a draft iteration
    draft_id = generate_id()
    await db.execute(
        """INSERT INTO draft_iterations (id, article_id, iteration_number, raw_draft_md, humanized_draft_md, created_at)
           VALUES (?, ?, 1, '# Test Draft\n\nSome content.', '# Test Draft\n\nSome humanized content.', ?)""",
        (draft_id, article_id, now),
    )
    # Create a pending review
    review_id = generate_id()
    await db.execute(
        """INSERT INTO article_reviews (id, article_id, review_number, status, created_at)
           VALUES (?, ?, 1, 'pending', ?)""",
        (review_id, article_id, now),
    )
    await db.commit()
    return article_id


async def setup_authed_client(config):
    """Set up migrations, create user, create session, return (app, session_id, user_id)."""
    from app.main import create_app
    app = create_app(config)
    async with get_connection(config.DATABASE_PATH) as db:
        await run_migrations(db)
        user_id = await create_test_user(db)
        session_id = await create_session(db, user_id, "full")
    return app, session_id, user_id


# ── Auth enforcement (applies to all dashboard endpoints) ──

class TestAuthEnforcement:
    """All dashboard endpoints must return 401 without a valid session cookie."""

    # (method, path, body) — body needed for Pydantic-validated POST endpoints
    ENDPOINTS = [
        ("GET", "/api/articles", None),
        ("GET", "/api/batches", None),
        ("GET", "/api/batches/fake_id", None),
        ("GET", "/api/pending-ideas", None),
        ("POST", "/api/seeds", {"seeds": [{"seed_type": "topic", "content": "test"}]}),
        ("GET", "/api/checkpoints/ideas/fake_id", None),
        ("POST", "/api/checkpoints/ideas/approve", {"batch_id": "fake", "approved_ideas": []}),
        ("GET", "/api/checkpoints/article/fake_id", None),
        ("POST", "/api/checkpoints/article/approve", {"article_id": "fake"}),
        ("POST", "/api/checkpoints/article/revise", {"article_id": "fake", "revision_notes": "x" * 25}),
        ("GET", "/api/settings", None),
        ("GET", "/api/usage", None),
        ("POST", "/api/articles/fake_id/cancel", None),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path,body", ENDPOINTS)
    async def test_endpoint_requires_auth(self, config, method, path, body):
        """Every dashboard endpoint returns 401 without session cookie."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            if method == "GET":
                resp = await client.get(path)
            else:
                resp = await client.post(path, json=body or {})
            assert resp.status_code == 401, f"{method} {path} should return 401 without auth, got {resp.status_code}"


# ── Articles API ──

class TestArticlesAPI:
    """Tests for GET /api/articles."""

    @pytest.mark.asyncio
    async def test_articles_returns_empty_list(self, config):
        """New user gets empty articles list."""
        from httpx import AsyncClient, ASGITransport
        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            assert resp.status_code == 200
            assert resp.json()["articles"] == []

    @pytest.mark.asyncio
    async def test_articles_returns_correct_fields(self, config):
        """GET /api/articles returns articles with all computed fields."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            await create_test_article(db, user_id, state="DRAFTING", seo_meta='{"focus_keyword":"test"}')

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            assert resp.status_code == 200
            data = resp.json()
            assert "articles" in data
            assert len(data["articles"]) == 1

            article = data["articles"][0]
            # Required fields for Dashboard cards
            assert "id" in article
            assert "title" in article
            assert article["state"] == "DRAFTING"
            assert article["column"] == "in_production"
            assert article["state_label"] == "Writing"
            assert article["has_seo"] is True
            assert "image_count" in article
            assert "valid_image_count" in article
            assert "keyword" in article

    @pytest.mark.asyncio
    async def test_articles_has_seo_false_when_null(self, config):
        """has_seo is False when seo_meta is null."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            await create_test_article(db, user_id, state="DRAFTING", seo_meta=None)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            article = resp.json()["articles"][0]
            assert article["has_seo"] is False

    @pytest.mark.asyncio
    async def test_articles_has_seo_false_when_empty_object(self, config):
        """has_seo is False when seo_meta is '{}'."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            await create_test_article(db, user_id, state="DRAFTING", seo_meta="{}")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            article = resp.json()["articles"][0]
            assert article["has_seo"] is False

    @pytest.mark.asyncio
    async def test_articles_image_counts(self, config):
        """image_count and valid_image_count computed correctly."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="DRAFTING")
            await create_test_images(db, article_id, total=5, valid=3)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            article = resp.json()["articles"][0]
            assert article["image_count"] == 5
            assert article["valid_image_count"] == 3

    @pytest.mark.asyncio
    async def test_articles_zero_images(self, config):
        """Articles with no images show 0/0 counts."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            await create_test_article(db, user_id, state="DRAFTING")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            article = resp.json()["articles"][0]
            assert article["image_count"] == 0
            assert article["valid_image_count"] == 0

    @pytest.mark.asyncio
    async def test_articles_state_column_mapping(self, config):
        """All states map to correct kanban columns."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        expected_mapping = {
            "OUTLINING": "in_production",
            "DRAFTING": "in_production",
            "HUMANIZING": "in_production",
            "EDIT_REVIEW": "in_production",
            "MEDIA_ASSEMBLY": "in_production",
            "WAITING_CHECKPOINT_2": "in_review",
            "REVISION": "in_production",
            "READY_TO_PUBLISH": "scheduled",
            "PUBLISHING": "scheduled",
            "PUBLISHED": "published",
            "FAILED": "in_production",
            "ARCHIVED": "archived",
        }

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            for state in expected_mapping:
                await create_test_article(db, user_id, state=state)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            articles = resp.json()["articles"]
            column_map = {a["state"]: a["column"] for a in articles}
            for state, expected_col in expected_mapping.items():
                assert column_map[state] == expected_col, \
                    f"State {state} should map to {expected_col}, got {column_map.get(state)}"

    @pytest.mark.asyncio
    async def test_articles_state_labels(self, config):
        """All states produce correct human-readable labels."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        expected_labels = {
            "OUTLINING": "Outlining",
            "DRAFTING": "Writing",
            "HUMANIZING": "Humanizing",
            "EDIT_REVIEW": "Editing",
            "MEDIA_ASSEMBLY": "Adding Images",
            "WAITING_CHECKPOINT_2": "Ready for Review",
            "REVISION": "Revising",
            "READY_TO_PUBLISH": "Scheduled",
            "PUBLISHING": "Publishing",
            "PUBLISHED": "Published",
            "FAILED": "Failed",
            "ARCHIVED": "Archived",
        }

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            for state in expected_labels:
                await create_test_article(db, user_id, state=state)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            articles = resp.json()["articles"]
            label_map = {a["state"]: a["state_label"] for a in articles}
            for state, expected_label in expected_labels.items():
                assert label_map[state] == expected_label, \
                    f"State {state} label should be '{expected_label}', got '{label_map.get(state)}'"

    @pytest.mark.asyncio
    async def test_articles_ordered_by_created_at_desc(self, config):
        """Articles returned newest first."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            await create_test_article(db, user_id, state="DRAFTING", title="First")
            await create_test_article(db, user_id, state="DRAFTING", title="Second")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            articles = resp.json()["articles"]
            assert len(articles) == 2
            assert articles[0]["title"] == "Second"  # newest first

    @pytest.mark.asyncio
    async def test_articles_isolated_per_user(self, config):
        """User A cannot see User B's articles."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_a = await create_test_user(db, email="a@example.com")
            user_b = await create_test_user(db, email="b@example.com")
            session_b = await create_session(db, user_b, "full")
            await create_test_article(db, user_a, state="DRAFTING", title="A's Article")
            await create_test_article(db, user_b, state="DRAFTING", title="B's Article")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_b}) as client:
            resp = await client.get("/api/articles")
            articles = resp.json()["articles"]
            assert len(articles) == 1
            assert articles[0]["title"] == "B's Article"


# ── Batches API ──

class TestBatchesAPI:
    """Tests for GET /api/batches and GET /api/batches/{id}."""

    @pytest.mark.asyncio
    async def test_batches_returns_batches_with_seed_count(self, config):
        """GET /api/batches returns batch list with correct seed counts."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            await create_test_batch(db, user_id, status="pending_ideation", seed_count=5)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/batches")
            assert resp.status_code == 200
            data = resp.json()
            assert "batches" in data
            assert len(data["batches"]) == 1
            batch = data["batches"][0]
            assert batch["status"] == "pending_ideation"
            assert batch["seed_count"] == 5
            assert "id" in batch
            assert "created_at" in batch

    @pytest.mark.asyncio
    async def test_batches_limited_to_10(self, config):
        """GET /api/batches returns at most 10 batches."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            for _ in range(15):
                await create_test_batch(db, user_id, status="processed", seed_count=1)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/batches")
            assert len(resp.json()["batches"]) == 10

    @pytest.mark.asyncio
    async def test_get_batch_by_id(self, config):
        """GET /api/batches/{id} returns batch with seeds and idea_count."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            batch_id = await create_test_batch(db, user_id, status="processed", seed_count=3)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get(f"/api/batches/{batch_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == batch_id
            assert data["status"] == "processed"
            assert data["seed_count"] == 3
            assert "seeds" in data
            assert len(data["seeds"]) == 3
            assert "idea_count" in data
            # Each seed has expected fields
            seed = data["seeds"][0]
            assert "id" in seed
            assert "content" in seed
            assert "seed_type" in seed

    @pytest.mark.asyncio
    async def test_get_batch_not_found(self, config):
        """GET /api/batches/{id} returns 404 for nonexistent batch."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/batches/nonexistent_id")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_other_users_batch_returns_404(self, config):
        """Cannot access another user's batch."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_a = await create_test_user(db, email="a@example.com")
            user_b = await create_test_user(db, email="b@example.com")
            session_b = await create_session(db, user_b, "full")
            batch_id = await create_test_batch(db, user_a, status="processed", seed_count=2)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_b}) as client:
            resp = await client.get(f"/api/batches/{batch_id}")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_batches_ordered_by_created_at_desc(self, config):
        """Batches returned newest first."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            b1 = await create_test_batch(db, user_id, status="processed", seed_count=1)
            b2 = await create_test_batch(db, user_id, status="pending_ideation", seed_count=2)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/batches")
            batches = resp.json()["batches"]
            assert batches[0]["id"] == b2  # newest first


# ── Pending Ideas API ──

class TestPendingIdeasAPI:
    """Tests for GET /api/pending-ideas."""

    @pytest.mark.asyncio
    async def test_pending_ideas_from_waiting_approval_batches(self, config):
        """Returns pending ideas only from waiting_approval batches."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")

            batch_id = generate_id()
            now = utc_now()
            await db.execute(
                "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'waiting_approval', ?)",
                (batch_id, user_id, now),
            )
            seed_id = generate_id()
            await db.execute(
                "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', 'test', ?)",
                (seed_id, batch_id, now),
            )
            idea_id = generate_id()
            await db.execute(
                """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
                   VALUES (?, ?, ?, 'Pending Idea', 'angle', 'kw', 'pending', ?)""",
                (idea_id, batch_id, seed_id, now),
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/pending-ideas")
            assert resp.status_code == 200
            data = resp.json()
            assert "ideas" in data
            assert "batches" in data
            assert len(data["ideas"]) == 1
            idea = data["ideas"][0]
            assert idea["title"] == "Pending Idea"
            assert idea["batch_id"] == batch_id
            assert "angle" in idea
            assert "target_keyword" in idea
            # Verify batch grouping
            assert len(data["batches"]) == 1
            batch = data["batches"][0]
            assert batch["batch_id"] == batch_id
            assert batch["idea_count"] == 1
            assert len(batch["ideas"]) == 1
            assert batch["ideas"][0]["title"] == "Pending Idea"

    @pytest.mark.asyncio
    async def test_pending_ideas_excludes_processed_batches(self, config):
        """Ideas from processed batches are not returned."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")

            batch_id = generate_id()
            now = utc_now()
            await db.execute(
                "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'processed', ?)",
                (batch_id, user_id, now),
            )
            seed_id = generate_id()
            await db.execute(
                "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', 'test', ?)",
                (seed_id, batch_id, now),
            )
            await db.execute(
                """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
                   VALUES (?, ?, ?, 'Processed Idea', 'angle', 'kw', 'approved', ?)""",
                (generate_id(), batch_id, seed_id, now),
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/pending-ideas")
            data = resp.json()
            assert len(data["ideas"]) == 0
            assert len(data["batches"]) == 0

    @pytest.mark.asyncio
    async def test_pending_ideas_empty_for_new_user(self, config):
        """New user has no pending ideas."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/pending-ideas")
            assert resp.status_code == 200
            data = resp.json()
            assert data["ideas"] == []
            assert data["batches"] == []

    @pytest.mark.asyncio
    async def test_pending_ideas_multiple_batches_grouped(self, config):
        """Multiple batches each group their own ideas correctly."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            now = utc_now()

            batch1_id = generate_id()
            batch2_id = generate_id()
            for bid in [batch1_id, batch2_id]:
                await db.execute(
                    "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'waiting_approval', ?)",
                    (bid, user_id, now),
                )

            # Batch 1: 3 ideas
            for i in range(3):
                sid = generate_id()
                await db.execute(
                    "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', 'x', ?)",
                    (sid, batch1_id, now),
                )
                await db.execute(
                    """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
                       VALUES (?, ?, ?, ?, 'angle', 'kw', 'pending', ?)""",
                    (generate_id(), batch1_id, sid, f"B1 Idea {i}", now),
                )
            # Batch 2: 2 ideas
            for i in range(2):
                sid = generate_id()
                await db.execute(
                    "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', 'x', ?)",
                    (sid, batch2_id, now),
                )
                await db.execute(
                    """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
                       VALUES (?, ?, ?, ?, 'angle', 'kw', 'pending', ?)""",
                    (generate_id(), batch2_id, sid, f"B2 Idea {i}", now),
                )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/pending-ideas")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["ideas"]) == 5
            assert len(data["batches"]) == 2
            counts = sorted([b["idea_count"] for b in data["batches"]])
            assert counts == [2, 3]
            for batch in data["batches"]:
                assert len(batch["ideas"]) == batch["idea_count"]
                assert "batch_created_at" in batch
                for idea in batch["ideas"]:
                    assert "id" in idea
                    assert "title" in idea
                    assert "angle" in idea
                    assert "target_keyword" in idea


# ── Seeds API (POST /api/seeds) ──

class TestSeedsAPI:
    """Tests for POST /api/seeds — batch creation."""

    @pytest.mark.asyncio
    async def test_create_batch_with_seed_type(self, config):
        """POST /api/seeds creates a batch using seed_type field."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/seeds", json={
                "seeds": [
                    {"seed_type": "topic", "content": "How to build a SaaS"},
                    {"seed_type": "topic", "content": "AI in healthcare"},
                ]
            })
            assert resp.status_code == 201
            data = resp.json()
            assert "batch_id" in data
            assert data["seed_count"] == 2

    @pytest.mark.asyncio
    async def test_create_batch_with_type_alias(self, config):
        """POST /api/seeds accepts 'type' as alias for 'seed_type'."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/seeds", json={
                "seeds": [{"type": "topic", "content": "Startup growth hacks"}]
            })
            assert resp.status_code == 201
            assert resp.json()["seed_count"] == 1

    @pytest.mark.asyncio
    async def test_create_batch_url_seed_type(self, config):
        """POST /api/seeds accepts URL seed type with valid URL."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/seeds", json={
                "seeds": [{"seed_type": "url", "content": "https://example.com/article"}]
            })
            assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_batch_empty_seeds_rejected(self, config):
        """POST /api/seeds rejects empty seeds list."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/seeds", json={"seeds": []})
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_batch_over_10_seeds_rejected(self, config):
        """POST /api/seeds rejects more than 10 seeds."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        seeds = [{"seed_type": "topic", "content": f"Topic {i}"} for i in range(11)]
        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/seeds", json={"seeds": seeds})
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_batch_empty_content_rejected(self, config):
        """POST /api/seeds rejects seeds with empty content."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/seeds", json={
                "seeds": [{"seed_type": "topic", "content": "   "}]
            })
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_batch_invalid_url_rejected(self, config):
        """POST /api/seeds rejects URL type with non-URL content."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/seeds", json={
                "seeds": [{"seed_type": "url", "content": "not a url"}]
            })
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_batch_requires_active_subscription(self, config):
        """POST /api/seeds rejects users without active subscription."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db, email="inactive@example.com", subscription_status="cancelled")
            session_id = await create_session(db, user_id, "full")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/seeds", json={
                "seeds": [{"seed_type": "topic", "content": "Test topic"}]
            })
            assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_create_batch_requires_ghost_connection(self, config):
        """POST /api/seeds rejects users without valid Ghost connection."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db, email="noghost@example.com", ghost_key_valid=0)
            session_id = await create_session(db, user_id, "full")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/seeds", json={
                "seeds": [{"seed_type": "topic", "content": "Test topic"}]
            })
            assert resp.status_code == 400


# ── Checkpoint 1 API ──

class TestCheckpoint1API:
    """Tests for GET /api/checkpoints/ideas/{batch_id} and POST /api/checkpoints/ideas/approve."""

    @pytest.mark.asyncio
    async def test_get_ideas_for_batch(self, config):
        """GET /api/checkpoints/ideas/{batch_id} returns ideas for review."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")

            batch_id = generate_id()
            now = utc_now()
            await db.execute(
                "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'waiting_approval', ?)",
                (batch_id, user_id, now),
            )
            seed_id = generate_id()
            await db.execute(
                "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', 'test', ?)",
                (seed_id, batch_id, now),
            )
            idea_id = generate_id()
            await db.execute(
                """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
                   VALUES (?, ?, ?, 'Review Idea', 'angle', 'keyword', 'pending', ?)""",
                (idea_id, batch_id, seed_id, now),
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get(f"/api/checkpoints/ideas/{batch_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["batch_id"] == batch_id
            assert data["status"] == "waiting_approval"
            assert data["read_only"] is False
            assert len(data["ideas"]) == 1
            assert data["ideas"][0]["title"] == "Review Idea"

    @pytest.mark.asyncio
    async def test_get_ideas_processed_batch_is_read_only(self, config):
        """Processed batch ideas are returned as read_only."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")

            batch_id = generate_id()
            now = utc_now()
            await db.execute(
                "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'processed', ?)",
                (batch_id, user_id, now),
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get(f"/api/checkpoints/ideas/{batch_id}")
            assert resp.status_code == 200
            assert resp.json()["read_only"] is True

    @pytest.mark.asyncio
    async def test_get_ideas_nonexistent_batch_404(self, config):
        """GET ideas for nonexistent batch returns 404."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/checkpoints/ideas/nonexistent")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_ideas_for_processed_batch_fails(self, config):
        """Cannot approve ideas for already-processed batch."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")

            batch_id = generate_id()
            now = utc_now()
            await db.execute(
                "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'processed', ?)",
                (batch_id, user_id, now),
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/checkpoints/ideas/approve", json={
                "batch_id": batch_id,
                "approved_ideas": [{"id": "fake_id"}],
            })
            assert resp.status_code == 400


# ── Checkpoint 2 API ──

class TestCheckpoint2API:
    """Tests for GET /api/checkpoints/article/{id}, approve, revise."""

    @pytest.mark.asyncio
    async def test_get_article_preview(self, config):
        """GET /api/checkpoints/article/{id} returns article preview for CP2."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_waiting_checkpoint2_article(db, user_id)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get(f"/api/checkpoints/article/{article_id}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["article_id"] == article_id
            assert data["state"] == "WAITING_CHECKPOINT_2"
            assert data["read_only"] is False
            assert "draft_html" in data
            assert len(data["draft_html"]) > 0
            assert "images" in data
            assert "seo" in data
            assert "review_history" in data
            assert "budget_remaining" in data

    @pytest.mark.asyncio
    async def test_get_article_preview_404(self, config):
        """GET /api/checkpoints/article/{id} returns 404 for nonexistent article."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/checkpoints/article/nonexistent")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_article_transitions_to_ready_to_publish(self, config):
        """Approving at CP2 moves article to READY_TO_PUBLISH."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_waiting_checkpoint2_article(db, user_id)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/checkpoints/article/approve", json={
                "article_id": article_id,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "approved"
            assert "scheduled_publish_at" in data

    @pytest.mark.asyncio
    async def test_approve_non_review_article_fails(self, config):
        """Cannot approve an article not in WAITING_CHECKPOINT_2 state."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="DRAFTING")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/checkpoints/article/approve", json={
                "article_id": article_id,
            })
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_revise_article_transitions_to_revision(self, config):
        """Requesting revision at CP2 moves article to REVISION."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_waiting_checkpoint2_article(db, user_id)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/checkpoints/article/revise", json={
                "article_id": article_id,
                "revision_notes": "Please improve the introduction and add more examples throughout.",
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == "revision_requested"

    @pytest.mark.asyncio
    async def test_revise_short_notes_rejected(self, config):
        """Revision notes under 20 chars are rejected."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_waiting_checkpoint2_article(db, user_id)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/checkpoints/article/revise", json={
                "article_id": article_id,
                "revision_notes": "Fix it",
            })
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_article_preview_read_only_for_non_review_state(self, config):
        """Article preview is read_only when not in WAITING_CHECKPOINT_2."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="PUBLISHED")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get(f"/api/checkpoints/article/{article_id}")
            assert resp.status_code == 200
            assert resp.json()["read_only"] is True


# ── Cancel Article API ──

class TestCancelArticleAPI:
    """Tests for POST /api/articles/{id}/cancel."""

    @pytest.mark.asyncio
    async def test_cancel_archives_article(self, config):
        """Cancelling an article changes state to ARCHIVED."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="DRAFTING")
            await create_usage_ledger(db, user_id, articles_started=1)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/cancel")
            assert resp.status_code == 200
            assert resp.json()["status"] == "archived"

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
            row = await cursor.fetchone()
            assert row["state"] == "ARCHIVED"

    @pytest.mark.asyncio
    async def test_cancel_published_article_fails(self, config):
        """Cannot cancel a published article."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="PUBLISHED")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/cancel")
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_archived_article_fails(self, config):
        """Cannot cancel an already archived article."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="ARCHIVED")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/cancel")
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_article_404(self, config):
        """Cancelling a nonexistent article returns 404."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/articles/nonexistent/cancel")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_other_users_article_404(self, config):
        """Cannot cancel another user's article."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user1_id = await create_test_user(db, email="user1@example.com")
            user2_id = await create_test_user(db, email="user2@example.com")
            session_id = await create_session(db, user2_id, "full")
            article_id = await create_test_article(db, user1_id, state="DRAFTING")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/cancel")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_cancel_restores_budget(self, config):
        """Cancelling an article decrements articles_started in usage ledger."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="DRAFTING")
            await create_usage_ledger(db, user_id, articles_started=3)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/cancel")
            assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT articles_started FROM usage_ledger WHERE user_id = ?", (user_id,)
            )
            row = await cursor.fetchone()
            assert row["articles_started"] == 2


# ── Settings API ──

class TestSettingsAPI:
    """Tests for GET /api/settings."""

    @pytest.mark.asyncio
    async def test_settings_returns_safe_fields(self, config):
        """GET /api/settings returns user settings without sensitive data."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/settings")
            assert resp.status_code == 200
            data = resp.json()
            # Expected safe fields
            assert "email" in data
            assert "ghost_key_valid" in data
            assert "subscription_status" in data
            assert "publish_days" in data
            assert "publish_time" in data
            assert "publish_timezone" in data
            # id is present (needed for PostHog identify)
            assert "id" in data
            # Sensitive fields must NOT be present
            assert "ghost_admin_api_key" not in data

    @pytest.mark.asyncio
    async def test_settings_returns_correct_values(self, config):
        """Settings values match what was set during user creation."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db, email="settings@example.com",
                                             publish_time="14:00",
                                             publish_timezone="Europe/London")
            session_id = await create_session(db, user_id, "full")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/settings")
            data = resp.json()
            assert data["email"] == "settings@example.com"
            assert data["publish_time"] == "14:00"
            assert data["publish_timezone"] == "Europe/London"


# ── Usage API ──

class TestUsageAPI:
    """Tests for GET /api/usage (timeline format)."""

    @pytest.mark.asyncio
    async def test_usage_returns_timeline_data(self, config):
        """GET /api/usage returns cycle, current_cycle, articles, previous_cycles."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            await create_usage_ledger(db, user_id, articles_started=5)
            await create_test_article(db, user_id, state="DRAFTING", title="Usage Article")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/usage")
            assert resp.status_code == 200
            data = resp.json()
            assert "cycle" in data
            assert "current_cycle" in data
            assert "articles" in data
            assert "previous_cycles" in data
            # Cycle info
            assert "start" in data["cycle"]
            assert "end" in data["cycle"]
            assert "days_left" in data["cycle"]
            assert "articles_limit" in data["cycle"]
            assert data["cycle"]["articles_limit"] == 10
            # Current cycle counts
            assert "published" in data["current_cycle"]
            assert "failed" in data["current_cycle"]
            assert "in_progress" in data["current_cycle"]
            assert "available" in data["current_cycle"]
            assert data["current_cycle"]["in_progress"] >= 1  # DRAFTING article
            # Articles
            assert len(data["articles"]) >= 1
            article = data["articles"][0]
            assert "id" in article
            assert "state" in article
            assert "title" in article
            assert "published_at" in article
            assert "scheduled_publish_at" in article

    @pytest.mark.asyncio
    async def test_usage_no_ledger_falls_back(self, config):
        """GET /api/usage with no ledger falls back to user created_at."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/usage")
            assert resp.status_code == 200
            data = resp.json()
            assert "cycle" in data
            assert "current_cycle" in data
            assert "articles" in data
            assert "previous_cycles" in data
            assert data["cycle"]["days_left"] >= 0


# ── Security Headers ──

class TestSecurityHeaders:
    """Verify security headers are present on API responses."""

    @pytest.mark.asyncio
    async def test_response_has_content_type_json(self, config):
        """API responses return application/json content type."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.get("/api/articles")
            assert "application/json" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_401_response_is_json(self, config):
        """401 responses are JSON, not HTML."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/articles")
            assert resp.status_code == 401
            assert "application/json" in resp.headers.get("content-type", "")
            data = resp.json()
            assert "detail" in data


class TestRetryArticleAPI:
    """Tests for POST /api/articles/{id}/retry (user-facing retry, Trello #340)."""

    @pytest.mark.asyncio
    async def test_retry_resumes_from_failed_from_state(self, config):
        """Retry resets state to failed_from_state and clears failure fields."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="FAILED")
            await db.execute(
                """UPDATE articles SET failed_from_state = 'HUMANIZING',
                   failure_reason = 'anthropic: Timeout after retry',
                   failed_at = ?, locked_by = 'worker-01' WHERE id = ?""",
                (utc_now(), article_id),
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/retry")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "retrying"
            assert body["resumed_from"] == "HUMANIZING"

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT state, failure_reason, failed_at, locked_by FROM articles WHERE id = ?",
                (article_id,),
            )
            row = dict(await cursor.fetchone())
            assert row["state"] == "HUMANIZING"
            assert row["failure_reason"] is None
            assert row["failed_at"] is None
            assert row["locked_by"] is None

    @pytest.mark.asyncio
    async def test_retry_falls_back_to_outlining_when_failed_from_state_null(self, config):
        """Legacy FAILED articles without failed_from_state retry from OUTLINING."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="FAILED")
            # failed_from_state stays NULL (legacy row)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/retry")
            assert resp.status_code == 200
            assert resp.json()["resumed_from"] == "OUTLINING"

    @pytest.mark.asyncio
    async def test_retry_falls_back_when_failed_from_state_unsafe(self, config):
        """If failed_from_state is a terminal/unsafe state, fall back to OUTLINING."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="FAILED")
            await db.execute(
                "UPDATE articles SET failed_from_state = 'WAITING_CHECKPOINT_2' WHERE id = ?",
                (article_id,),
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/retry")
            assert resp.status_code == 200
            assert resp.json()["resumed_from"] == "OUTLINING"

    @pytest.mark.asyncio
    async def test_retry_non_failed_article_returns_400(self, config):
        """Only FAILED articles can be retried."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="DRAFTING")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/retry")
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_retry_archived_article_returns_400(self, config):
        """ARCHIVED (user-cancelled) articles cannot be retried."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="ARCHIVED")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/retry")
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_retry_nonexistent_article_404(self, config):
        """Retrying a nonexistent article returns 404."""
        from httpx import AsyncClient, ASGITransport

        app, session_id, _ = await setup_authed_client(config)
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post("/api/articles/nonexistent/retry")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_other_users_article_404(self, config):
        """Cannot retry another user's article."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user1_id = await create_test_user(db, email="user1@example.com")
            user2_id = await create_test_user(db, email="user2@example.com")
            session_id = await create_session(db, user2_id, "full")
            article_id = await create_test_article(db, user1_id, state="FAILED")

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/retry")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_retry_is_idempotent(self, config):
        """Second retry call on an already-retried article returns already_retried."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="FAILED")
            await db.execute(
                "UPDATE articles SET failed_from_state = 'DRAFTING' WHERE id = ?",
                (article_id,),
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp1 = await client.post(f"/api/articles/{article_id}/retry")
            assert resp1.status_code == 200
            assert resp1.json()["status"] == "retrying"

            # Article is now in DRAFTING — not FAILED. A second call should
            # return 400 because the article is no longer in FAILED state.
            resp2 = await client.post(f"/api/articles/{article_id}/retry")
            assert resp2.status_code == 400

    @pytest.mark.asyncio
    async def test_retry_logs_pipeline_event(self, config):
        """Retry logs a pipeline_event with event_type='retry'."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="FAILED")
            await db.execute(
                "UPDATE articles SET failed_from_state = 'MEDIA_ASSEMBLY' WHERE id = ?",
                (article_id,),
            )
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/retry")
            assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                """SELECT event_type, from_state, to_state FROM pipeline_events
                   WHERE article_id = ? AND event_type = 'retry'""",
                (article_id,),
            )
            rows = [dict(r) for r in await cursor.fetchall()]
            assert len(rows) == 1
            assert rows[0]["from_state"] == "FAILED"
            assert rows[0]["to_state"] == "MEDIA_ASSEMBLY"

    @pytest.mark.asyncio
    async def test_retry_does_not_recharge_budget(self, config):
        """Retrying does not increment articles_started in usage ledger."""
        from httpx import AsyncClient, ASGITransport
        from app.main import create_app

        app = create_app(config)
        transport = ASGITransport(app=app)

        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)
            user_id = await create_test_user(db)
            session_id = await create_session(db, user_id, "full")
            article_id = await create_test_article(db, user_id, state="FAILED")
            await db.execute(
                "UPDATE articles SET failed_from_state = 'HUMANIZING' WHERE id = ?",
                (article_id,),
            )
            await create_usage_ledger(db, user_id, articles_started=1)
            await db.commit()

        async with AsyncClient(transport=transport, base_url="http://test", cookies={"session_id": session_id}) as client:
            resp = await client.post(f"/api/articles/{article_id}/retry")
            assert resp.status_code == 200

        async with get_connection(config.DATABASE_PATH) as db:
            cursor = await db.execute(
                "SELECT articles_started FROM usage_ledger WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            assert row["articles_started"] == 1  # unchanged
