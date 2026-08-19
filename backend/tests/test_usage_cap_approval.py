"""Tests for usage-cap approval gate fix (Trello card: fix-usage-cap-approval).

Covers:
1. Archived articles do not block approval.
2. Stale ledger at 8/8 does not block when effective usage is 6/8.
3. Over-limit approval returns 409 and leaves ideas/batch intact.
4. Full-cap approval leaves ideas/batch intact and stale full ledger still blocks when effective usage is full.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.user import create_user, update_user
from app.models.seed_batch import create_seed_batch
from app.middleware.auth_middleware import create_session
from app.pipeline.transitions.t1_ideation import run_ideation
from app.llm.mock import MockLLM
from app.models.usage import (
    get_or_create_current_ledger,
    increment_articles_started,
    get_articles_remaining,
    ArticleLimitError,
    _get_effective_usage_in_cycle,
)
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *  # noqa: F401,F403


# ── helpers ──────────────────────────────────────────────────────────

async def _create_active_user(db):
    """Create a user with active subscription and ghost connected."""
    user = await create_user(db, f"cap-test-{generate_id()}@test.com")
    await update_user(
        db, user["id"],
        subscription_status="active",
        ghost_key_valid=1,
        ghost_url="https://blog.example.com",
    )
    await db.execute(
        "UPDATE users SET articles_per_cycle_limit = 8 WHERE id = ?",
        (user["id"],),
    )
    await db.commit()
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
    return dict(await cursor.fetchone())


async def _insert_article_for_user(db, user_id: str, state: str) -> str:
    """Insert a bare article row for counting tests (no full ideation needed)."""
    batch_id = generate_id()
    idea_id = generate_id()
    seed_id = generate_id()
    article_id = generate_id()
    now = utc_now()

    await db.execute(
        """INSERT INTO seed_batches (id, user_id, status, created_at)
           VALUES (?, ?, 'processed', ?)""",
        (batch_id, user_id, now),
    )
    await db.execute(
        """INSERT INTO seeds (id, batch_id, seed_type, content, created_at)
           VALUES (?, ?, 'topic', 'test topic', ?)""",
        (seed_id, batch_id, now),
    )
    await db.execute(
        """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
           VALUES (?, ?, ?, 'Test Idea', 'Test angle', 'test keyword', 'approved', ?)""",
        (idea_id, batch_id, seed_id, now),
    )
    await db.execute(
        """INSERT INTO articles (id, user_id, idea_id, state, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (article_id, user_id, idea_id, state, now, now),
    )
    await db.commit()
    return article_id


# ── Test 1: ARCHIVED articles excluded from effective usage ──────────

class TestArchivedDoesNotCount:
    @pytest.mark.asyncio
    async def test_archived_excluded_from_effective_usage(self, db):
        """ARCHIVED articles do not count toward effective cycle usage."""
        user = await _create_active_user(db)
        ledger = await get_or_create_current_ledger(db, user["id"])
        cycle_start = ledger["billing_cycle_start"]

        # Insert 5 published + 1 ARCHIVED
        for _ in range(5):
            await _insert_article_for_user(db, user["id"], "PUBLISHED")
        await _insert_article_for_user(db, user["id"], "ARCHIVED")

        effective = await _get_effective_usage_in_cycle(db, user["id"], cycle_start)
        assert effective == 5, f"Expected 5, got {effective} (ARCHIVED should not count)"

    @pytest.mark.asyncio
    async def test_archived_does_not_raise_limit_error(self, db):
        """With 5 published + 1 ARCHIVED (6 total rows), approving 1 more should work."""
        user = await _create_active_user(db)
        await get_or_create_current_ledger(db, user["id"])

        for _ in range(5):
            await _insert_article_for_user(db, user["id"], "PUBLISHED")
        await _insert_article_for_user(db, user["id"], "ARCHIVED")

        # 5 effective used, limit is 8 → should NOT raise
        try:
            await increment_articles_started(db, user["id"])
        except ArticleLimitError:
            pytest.fail("ArticleLimitError raised unexpectedly — ARCHIVED article was counted")

    @pytest.mark.asyncio
    async def test_articles_remaining_excludes_archived(self, db):
        """get_articles_remaining correctly excludes ARCHIVED."""
        user = await _create_active_user(db)
        await get_or_create_current_ledger(db, user["id"])

        for _ in range(5):
            await _insert_article_for_user(db, user["id"], "PUBLISHED")
        await _insert_article_for_user(db, user["id"], "ARCHIVED")

        remaining = await get_articles_remaining(db, user["id"])
        # 8 limit - 5 effective = 3 remaining
        assert remaining == 3, f"Expected 3 remaining, got {remaining}"


# ── Test 2: Stale ledger at 8/8 does not block valid approvals ───────

class TestStaleLedgerDoesNotBlock:
    @pytest.mark.asyncio
    async def test_stale_ledger_8_of_8_with_6_effective(self, db):
        """Stale ledger articles_started=8 does not block when effective usage is 6."""
        user = await _create_active_user(db)
        ledger = await get_or_create_current_ledger(db, user["id"])

        # Force ledger to stale 8/8
        await db.execute(
            "UPDATE usage_ledger SET articles_started = 8 WHERE id = ?",
            (ledger["id"],),
        )
        await db.commit()

        # Insert only 6 counted articles (simulating 2 were archived)
        for _ in range(5):
            await _insert_article_for_user(db, user["id"], "PUBLISHED")
        await _insert_article_for_user(db, user["id"], "OUTLINING")

        # Effective = 6, limit = 8 → should NOT raise even with stale ledger at 8
        try:
            await increment_articles_started(db, user["id"])
        except ArticleLimitError:
            pytest.fail(
                "ArticleLimitError raised — stale ledger blocked valid approval "
                "(effective usage was 6/8)"
            )

    @pytest.mark.asyncio
    async def test_stale_ledger_approval_via_api(self, db, config):
        """Full API flow: stale ledger at 8/8 with effective 6/8 → approval succeeds."""
        from app.services.email import clear_sent_emails
        clear_sent_emails()

        user = await _create_active_user(db)
        ledger = await get_or_create_current_ledger(db, user["id"])

        # Stale ledger: 8/8
        await db.execute(
            "UPDATE usage_ledger SET articles_started = 8 WHERE id = ?",
            (ledger["id"],),
        )
        await db.commit()

        # 5 published + 1 in_review = 6 effective
        for _ in range(5):
            await _insert_article_for_user(db, user["id"], "PUBLISHED")
        await _insert_article_for_user(db, user["id"], "OUTLINING")

        # Create a batch with ideas to approve
        seeds = [{"seed_type": "topic", "content": "AI testing strategies"}]
        batch_id, _ = await create_seed_batch(db, user["id"], seeds)
        await run_ideation(db, config, batch_id, MockLLM())

        cursor = await db.execute(
            "SELECT id FROM ideas WHERE batch_id = ? LIMIT 1", (batch_id,)
        )
        idea_id = (await cursor.fetchone())["id"]

        session_id = await create_session(db, user["id"], "full")

        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/checkpoints/ideas/approve",
            json={"batch_id": batch_id, "approved_ideas": [{"id": idea_id}]},
            cookies={"session_id": session_id},
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["articles_created"] == 1
        assert data.get("budget_limited") is not True


# ── Test 3: Over-limit approval returns 409, ideas/batch intact ──────

class TestOverLimitReturns409:
    @pytest.mark.asyncio
    async def test_full_cap_returns_409(self, db, config):
        """When at true capacity (8/8 effective), approval returns 409."""
        from app.services.email import clear_sent_emails
        clear_sent_emails()

        user = await _create_active_user(db)
        await get_or_create_current_ledger(db, user["id"])

        # Fill up 8 real articles
        for _ in range(8):
            await _insert_article_for_user(db, user["id"], "PUBLISHED")

        seeds = [{"seed_type": "topic", "content": "Over-limit test topic"}]
        batch_id, _ = await create_seed_batch(db, user["id"], seeds)
        await run_ideation(db, config, batch_id, MockLLM())

        cursor = await db.execute(
            "SELECT id FROM ideas WHERE batch_id = ? LIMIT 1", (batch_id,)
        )
        idea_id = (await cursor.fetchone())["id"]

        session_id = await create_session(db, user["id"], "full")

        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/checkpoints/ideas/approve",
            json={"batch_id": batch_id, "approved_ideas": [{"id": idea_id}]},
            cookies={"session_id": session_id},
        )
        assert response.status_code == 409, (
            f"Expected 409, got {response.status_code}: {response.text}"
        )

    @pytest.mark.asyncio
    async def test_full_cap_409_leaves_ideas_pending(self, db, config):
        """409 on full cap must NOT reject ideas or mark batch as processed."""
        from app.services.email import clear_sent_emails
        clear_sent_emails()

        user = await _create_active_user(db)
        await get_or_create_current_ledger(db, user["id"])

        # Fill capacity
        for _ in range(8):
            await _insert_article_for_user(db, user["id"], "PUBLISHED")

        seeds = [{"seed_type": "topic", "content": "Ideas must survive cap failure"}]
        batch_id, _ = await create_seed_batch(db, user["id"], seeds)
        await run_ideation(db, config, batch_id, MockLLM())

        cursor = await db.execute(
            "SELECT id FROM ideas WHERE batch_id = ? AND status = 'pending'", (batch_id,)
        )
        pending_before = [r["id"] for r in await cursor.fetchall()]
        assert len(pending_before) > 0, "Need pending ideas to test"

        idea_id = pending_before[0]
        session_id = await create_session(db, user["id"], "full")

        app = create_app(config)
        client = TestClient(app)
        client.post(
            "/api/checkpoints/ideas/approve",
            json={"batch_id": batch_id, "approved_ideas": [{"id": idea_id}]},
            cookies={"session_id": session_id},
        )

        # Idea must still be pending
        cursor = await db.execute("SELECT status FROM ideas WHERE id = ?", (idea_id,))
        idea_status = (await cursor.fetchone())["status"]
        assert idea_status == "pending", (
            f"Idea was mutated to '{idea_status}' — it should remain 'pending' after 409"
        )

        # Batch must NOT be processed
        cursor = await db.execute(
            "SELECT status FROM seed_batches WHERE id = ?", (batch_id,)
        )
        batch_status = (await cursor.fetchone())["status"]
        assert batch_status != "processed", (
            f"Batch was marked 'processed' after 409 — it should remain untouched"
        )

    @pytest.mark.asyncio
    async def test_stale_ledger_full_effective_still_409(self, db, config):
        """Stale ledger=8/8 AND effective=8/8 → still 409 (not a false positive)."""
        from app.services.email import clear_sent_emails
        clear_sent_emails()

        user = await _create_active_user(db)
        ledger = await get_or_create_current_ledger(db, user["id"])

        # Force stale ledger AND insert real 8 counted articles
        await db.execute(
            "UPDATE usage_ledger SET articles_started = 8 WHERE id = ?",
            (ledger["id"],),
        )
        for _ in range(8):
            await _insert_article_for_user(db, user["id"], "PUBLISHED")
        await db.commit()

        seeds = [{"seed_type": "topic", "content": "Still over cap"}]
        batch_id, _ = await create_seed_batch(db, user["id"], seeds)
        await run_ideation(db, config, batch_id, MockLLM())

        cursor = await db.execute(
            "SELECT id FROM ideas WHERE batch_id = ? LIMIT 1", (batch_id,)
        )
        idea_id = (await cursor.fetchone())["id"]
        session_id = await create_session(db, user["id"], "full")

        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/checkpoints/ideas/approve",
            json={"batch_id": batch_id, "approved_ideas": [{"id": idea_id}]},
            cookies={"session_id": session_id},
        )
        assert response.status_code == 409
