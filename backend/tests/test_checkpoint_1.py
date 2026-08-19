"""Tests for Checkpoint 1: idea approval + article creation (Task 2.4)."""

import json
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.config import Config
from app.database import get_connection, run_migrations
from app.main import create_app
from app.models.user import create_user, update_user
from app.models.seed_batch import create_seed_batch
from app.middleware.auth_middleware import create_session
from app.pipeline.transitions.t1_ideation import run_ideation
from app.llm.mock import MockLLM
from app.services.email import clear_sent_emails
from app.utils.ulid import generate_id
from app.utils.time import utc_now
from app.models.usage import get_or_create_current_ledger

from tests.conftest import *


@pytest_asyncio.fixture
async def setup_cp1(db, config):
    """Create user, batch, run ideation → ready for CP1."""
    clear_sent_emails()
    user = await create_user(db, "cp1@test.com")
    await update_user(
        db, user["id"],
        subscription_status="active",
        ghost_key_valid=1,
        ghost_url="https://blog.example.com",
    )
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
    user = dict(await cursor.fetchone())

    seeds = [
        {"seed_type": "topic", "content": "AI in healthcare"},
        {"seed_type": "topic", "content": "Python testing"},
    ]
    batch_id, _ = await create_seed_batch(db, user["id"], seeds)
    await run_ideation(db, config, batch_id, MockLLM())

    # Create session with checkpoint_1 scope
    session_id = await create_session(db, user["id"], "checkpoint_1", scope_ref=batch_id)
    # Also create a full session for testing
    full_session_id = await create_session(db, user["id"], "full")

    # Create usage ledger
    await get_or_create_current_ledger(db, user["id"])

    return {
        "user": user,
        "batch_id": batch_id,
        "session_id": session_id,
        "full_session_id": full_session_id,
    }


class TestGetIdeas:
    @pytest.mark.asyncio
    async def test_list_ideas(self, db, config, setup_cp1):
        app = create_app(config)
        client = TestClient(app)
        batch_id = setup_cp1["batch_id"]
        response = client.get(
            f"/api/checkpoints/ideas/{batch_id}",
            cookies={"session_id": setup_cp1["session_id"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["ideas"]) == 6  # 2 seeds × 3 ideas

    @pytest.mark.asyncio
    async def test_list_ideas_with_full_session(self, db, config, setup_cp1):
        """Full session can also access checkpoint pages."""
        app = create_app(config)
        client = TestClient(app)
        batch_id = setup_cp1["batch_id"]
        response = client.get(
            f"/api/checkpoints/ideas/{batch_id}",
            cookies={"session_id": setup_cp1["full_session_id"]},
        )
        assert response.status_code == 200


class TestApproveIdeas:
    @pytest.mark.asyncio
    async def test_approve_all(self, db, config, setup_cp1):
        app = create_app(config)
        client = TestClient(app)
        batch_id = setup_cp1["batch_id"]

        # Get ideas
        cursor = await db.execute("SELECT id FROM ideas WHERE batch_id = ?", (batch_id,))
        idea_ids = [row["id"] for row in await cursor.fetchall()]

        response = client.post(
            "/api/checkpoints/ideas/approve",
            json={
                "batch_id": batch_id,
                "approved_ideas": [{"id": iid} for iid in idea_ids],
            },
            cookies={"session_id": setup_cp1["session_id"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["articles_created"] == 6

        # Verify articles in OUTLINING
        cursor = await db.execute(
            "SELECT state FROM articles WHERE user_id = ?", (setup_cp1["user"]["id"],)
        )
        articles = await cursor.fetchall()
        assert all(a["state"] == "OUTLINING" for a in articles)

    @pytest.mark.asyncio
    async def test_approve_some_reject_others(self, db, config, setup_cp1):
        app = create_app(config)
        client = TestClient(app)
        batch_id = setup_cp1["batch_id"]

        cursor = await db.execute("SELECT id FROM ideas WHERE batch_id = ?", (batch_id,))
        idea_ids = [row["id"] for row in await cursor.fetchall()]

        # Approve first 2, reject rest
        response = client.post(
            "/api/checkpoints/ideas/approve",
            json={
                "batch_id": batch_id,
                "approved_ideas": [{"id": idea_ids[0]}, {"id": idea_ids[1]}],
            },
            cookies={"session_id": setup_cp1["session_id"]},
        )
        assert response.status_code == 200
        assert response.json()["articles_created"] == 2

        # Verify approved
        cursor = await db.execute("SELECT status FROM ideas WHERE id = ?", (idea_ids[0],))
        assert (await cursor.fetchone())["status"] == "approved"
        # Verify rejected
        cursor = await db.execute("SELECT status FROM ideas WHERE id = ?", (idea_ids[2],))
        assert (await cursor.fetchone())["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_edited_title_saved(self, db, config, setup_cp1):
        app = create_app(config)
        client = TestClient(app)
        batch_id = setup_cp1["batch_id"]

        cursor = await db.execute("SELECT id FROM ideas WHERE batch_id = ? LIMIT 1", (batch_id,))
        idea_id = (await cursor.fetchone())["id"]

        response = client.post(
            "/api/checkpoints/ideas/approve",
            json={
                "batch_id": batch_id,
                "approved_ideas": [{"id": idea_id, "title": "Custom Title"}],
            },
            cookies={"session_id": setup_cp1["session_id"]},
        )
        assert response.status_code == 200

        cursor = await db.execute("SELECT title FROM ideas WHERE id = ?", (idea_id,))
        assert (await cursor.fetchone())["title"] == "Custom Title"

    @pytest.mark.asyncio
    async def test_article_limit_enforced(self, db, config, setup_cp1):
        """Can't approve more articles than budget allows.

        Gate uses actual article states (not stale ledger). Insert 6 real articles
        to fill 6/8 capacity, leaving only 2 remaining.
        """
        app = create_app(config)
        client = TestClient(app)
        batch_id = setup_cp1["batch_id"]
        user_id = setup_cp1["user"]["id"]

        # Insert 6 real articles to consume 6 of 8 capacity slots
        now = utc_now()
        for _ in range(6):
            filler_batch_id = generate_id()
            filler_seed_id = generate_id()
            filler_idea_id = generate_id()
            filler_article_id = generate_id()
            await db.execute(
                "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'processed', ?)",
                (filler_batch_id, user_id, now),
            )
            await db.execute(
                "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', 'filler', ?)",
                (filler_seed_id, filler_batch_id, now),
            )
            await db.execute(
                "INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at) VALUES (?, ?, ?, 'Filler', 'Filler', 'kw', 'approved', ?)",
                (filler_idea_id, filler_batch_id, filler_seed_id, now),
            )
            await db.execute(
                "INSERT INTO articles (id, user_id, idea_id, state, created_at, updated_at) VALUES (?, ?, ?, 'PUBLISHED', ?, ?)",
                (filler_article_id, user_id, filler_idea_id, now, now),
            )
        await db.commit()

        cursor = await db.execute("SELECT id FROM ideas WHERE batch_id = ?", (batch_id,))
        idea_ids = [row["id"] for row in await cursor.fetchall()]

        # Try to approve all 6 (only 2 budget remaining)
        response = client.post(
            "/api/checkpoints/ideas/approve",
            json={
                "batch_id": batch_id,
                "approved_ideas": [{"id": iid} for iid in idea_ids],
            },
            cookies={"session_id": setup_cp1["session_id"]},
        )
        assert response.status_code == 409
        assert "2 articles remaining" in response.json()["detail"]

        # Over-limit approval rejects the whole request — no partial creation,
        # no idea burning, and no processed batch.
        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM articles a JOIN ideas i ON a.idea_id = i.id WHERE i.batch_id = ?",
            (batch_id,),
        )
        assert (await cursor.fetchone())["c"] == 0

        cursor = await db.execute(
            "SELECT COUNT(*) AS c FROM ideas WHERE batch_id = ? AND status = 'pending'",
            (batch_id,),
        )
        assert (await cursor.fetchone())["c"] == len(idea_ids)

        cursor = await db.execute("SELECT status FROM seed_batches WHERE id = ?", (batch_id,))
        assert (await cursor.fetchone())["status"] == "waiting_approval"

    @pytest.mark.asyncio
    async def test_expired_batch(self, db, config, setup_cp1):
        """Expired batch returns error."""
        app = create_app(config)
        client = TestClient(app)
        batch_id = setup_cp1["batch_id"]

        await db.execute(
            "UPDATE seed_batches SET status = 'expired' WHERE id = ?", (batch_id,)
        )
        await db.commit()

        cursor = await db.execute("SELECT id FROM ideas WHERE batch_id = ? LIMIT 1", (batch_id,))
        idea_id = (await cursor.fetchone())["id"]

        response = client.post(
            "/api/checkpoints/ideas/approve",
            json={
                "batch_id": batch_id,
                "approved_ideas": [{"id": idea_id}],
            },
            cookies={"session_id": setup_cp1["session_id"]},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_already_processed(self, db, config, setup_cp1):
        """Already processed batch returns read-only."""
        app = create_app(config)
        client = TestClient(app)
        batch_id = setup_cp1["batch_id"]

        await db.execute(
            "UPDATE seed_batches SET status = 'processed' WHERE id = ?", (batch_id,)
        )
        await db.commit()

        response = client.get(
            f"/api/checkpoints/ideas/{batch_id}",
            cookies={"session_id": setup_cp1["session_id"]},
        )
        assert response.status_code == 200
        assert response.json()["read_only"] is True
