"""Tests for worker (Task 2.2)."""

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.pipeline.worker import find_pending_batches, find_processable_articles
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *
from tests.test_locking import _create_article


async def _create_batch(db, user_id, status="pending_ideation"):
    batch_id = generate_id()
    now = utc_now()
    await db.execute(
        "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, ?, ?)",
        (batch_id, user_id, status, now),
    )
    await db.commit()
    return batch_id


class TestFindPendingBatches:
    @pytest.mark.asyncio
    async def test_finds_pending_batch(self, db):
        user = await create_user(db, "worker1@test.com")
        await update_user(db, user["id"], subscription_status="active")
        batch_id = await _create_batch(db, user["id"], "pending_ideation")
        batches = await find_pending_batches(db)
        assert len(batches) >= 1
        assert any(b["id"] == batch_id for b in batches)

    @pytest.mark.asyncio
    async def test_ignores_non_pending(self, db):
        user = await create_user(db, "worker2@test.com")
        await update_user(db, user["id"], subscription_status="active")
        await _create_batch(db, user["id"], "waiting_approval")
        batches = await find_pending_batches(db)
        assert len(batches) == 0

    @pytest.mark.asyncio
    async def test_ignores_inactive_subscription(self, db):
        user = await create_user(db, "worker3@test.com")
        await update_user(db, user["id"], subscription_status="canceled")
        await _create_batch(db, user["id"], "pending_ideation")
        batches = await find_pending_batches(db)
        assert len(batches) == 0


class TestFindProcessableArticles:
    @pytest.mark.asyncio
    async def test_finds_unlocked_article(self, db):
        user = await create_user(db, "worker4@test.com")
        await update_user(db, user["id"], subscription_status="active")
        article_id = await _create_article(db, user["id"], "OUTLINING")
        articles = await find_processable_articles(db, max_concurrent=10)
        assert len(articles) >= 1
        assert any(a["id"] == article_id for a in articles)

    @pytest.mark.asyncio
    async def test_respects_concurrency_cap(self, db):
        user = await create_user(db, "worker5@test.com")
        await update_user(db, user["id"], subscription_status="active")
        for _ in range(3):
            await _create_article(db, user["id"], "OUTLINING")
        # Only one per user (sequential processing)
        articles = await find_processable_articles(db, max_concurrent=10)
        user_articles = [a for a in articles if a["user_id"] == user["id"]]
        assert len(user_articles) == 1

    @pytest.mark.asyncio
    async def test_skips_checkpoint_states(self, db):
        user = await create_user(db, "worker6@test.com")
        await update_user(db, user["id"], subscription_status="active")
        await _create_article(db, user["id"], "WAITING_CHECKPOINT_2")
        articles = await find_processable_articles(db, max_concurrent=10)
        assert len(articles) == 0

    @pytest.mark.asyncio
    async def test_skips_terminal_states(self, db):
        user = await create_user(db, "worker7@test.com")
        await update_user(db, user["id"], subscription_status="active")
        await _create_article(db, user["id"], "PUBLISHED")
        articles = await find_processable_articles(db, max_concurrent=10)
        assert len(articles) == 0

    @pytest.mark.asyncio
    async def test_worker_idle_no_work(self, db):
        articles = await find_processable_articles(db, max_concurrent=10)
        assert len(articles) == 0

    @pytest.mark.asyncio
    async def test_different_users_parallel(self, db):
        user1 = await create_user(db, "worker8@test.com")
        user2 = await create_user(db, "worker9@test.com")
        await update_user(db, user1["id"], subscription_status="active")
        await update_user(db, user2["id"], subscription_status="active")
        await _create_article(db, user1["id"], "OUTLINING")
        await _create_article(db, user2["id"], "DRAFTING")
        articles = await find_processable_articles(db, max_concurrent=10)
        assert len(articles) == 2

    @pytest.mark.asyncio
    async def test_global_cap(self, db):
        """Global concurrency cap limits total articles."""
        for i in range(5):
            user = await create_user(db, f"cap{i}@test.com")
            await update_user(db, user["id"], subscription_status="active")
            await _create_article(db, user["id"], "OUTLINING")
        articles = await find_processable_articles(db, max_concurrent=3)
        assert len(articles) <= 3

    @pytest.mark.asyncio
    async def test_checkpointed_doesnt_block_next(self, db):
        """Article at checkpoint shouldn't block next article for same user."""
        user = await create_user(db, "worker10@test.com")
        await update_user(db, user["id"], subscription_status="active")
        await _create_article(db, user["id"], "WAITING_CHECKPOINT_2")
        await _create_article(db, user["id"], "OUTLINING")
        articles = await find_processable_articles(db, max_concurrent=10)
        user_articles = [a for a in articles if a["user_id"] == user["id"]]
        assert len(user_articles) == 1
        assert user_articles[0]["state"] == "OUTLINING"
