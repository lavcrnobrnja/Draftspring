"""Tests for article locking (Task 2.2)."""

import json

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.models.user import create_user
from app.pipeline.locking import acquire_lock, release_lock, cleanup_stale_locks
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *


async def _create_article(db, user_id, state="OUTLINING"):
    """Helper to insert an article directly."""
    idea_id = generate_id()
    batch_id = generate_id()
    now = utc_now()
    # Create batch and seed and idea first
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
        "INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at) VALUES (?, ?, ?, 'Test', 'Angle', 'kw', 'approved', ?)",
        (idea_id, batch_id, seed_id, now),
    )
    article_id = generate_id()
    await db.execute(
        """INSERT INTO articles (id, user_id, idea_id, state, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (article_id, user_id, idea_id, state, now, now),
    )
    await db.commit()
    return article_id


class TestAcquireLock:
    @pytest.mark.asyncio
    async def test_acquire_unlocked(self, db):
        user = await create_user(db, "lock@test.com")
        article_id = await _create_article(db, user["id"])
        result = await acquire_lock(db, article_id, "worker-01")
        assert result is True
        # Verify in DB
        cursor = await db.execute("SELECT locked_by, locked_at FROM articles WHERE id = ?", (article_id,))
        row = await cursor.fetchone()
        assert row["locked_by"] == "worker-01"
        assert row["locked_at"] is not None

    @pytest.mark.asyncio
    async def test_acquire_already_locked(self, db):
        user = await create_user(db, "lock2@test.com")
        article_id = await _create_article(db, user["id"])
        await acquire_lock(db, article_id, "worker-01")
        result = await acquire_lock(db, article_id, "worker-02")
        assert result is False

    @pytest.mark.asyncio
    async def test_same_worker_can_relock(self, db):
        """Same worker re-acquiring its own lock should succeed."""
        user = await create_user(db, "lock3@test.com")
        article_id = await _create_article(db, user["id"])
        await acquire_lock(db, article_id, "worker-01")
        result = await acquire_lock(db, article_id, "worker-01")
        assert result is True


class TestReleaseLock:
    @pytest.mark.asyncio
    async def test_release_lock(self, db):
        user = await create_user(db, "rel@test.com")
        article_id = await _create_article(db, user["id"])
        await acquire_lock(db, article_id, "worker-01")
        await release_lock(db, article_id, "worker-01")
        cursor = await db.execute("SELECT locked_by, locked_at FROM articles WHERE id = ?", (article_id,))
        row = await cursor.fetchone()
        assert row["locked_by"] is None
        assert row["locked_at"] is None

    @pytest.mark.asyncio
    async def test_release_by_different_worker_noop(self, db):
        """Only the owning worker can release."""
        user = await create_user(db, "rel2@test.com")
        article_id = await _create_article(db, user["id"])
        await acquire_lock(db, article_id, "worker-01")
        await release_lock(db, article_id, "worker-02")
        cursor = await db.execute("SELECT locked_by FROM articles WHERE id = ?", (article_id,))
        row = await cursor.fetchone()
        assert row["locked_by"] == "worker-01"  # Still locked


class TestStaleCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_stale_locks(self, db):
        user = await create_user(db, "stale@test.com")
        article_id = await _create_article(db, user["id"])
        # Set lock with old timestamp (15 minutes ago)
        from datetime import datetime, timezone, timedelta
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        await db.execute(
            "UPDATE articles SET locked_by = 'worker-old', locked_at = ? WHERE id = ?",
            (old_time, article_id),
        )
        await db.commit()
        cleaned = await cleanup_stale_locks(db, max_age_minutes=10)
        assert cleaned >= 1
        cursor = await db.execute("SELECT locked_by FROM articles WHERE id = ?", (article_id,))
        row = await cursor.fetchone()
        assert row["locked_by"] is None

    @pytest.mark.asyncio
    async def test_fresh_lock_not_cleaned(self, db):
        user = await create_user(db, "fresh@test.com")
        article_id = await _create_article(db, user["id"])
        await acquire_lock(db, article_id, "worker-01")
        cleaned = await cleanup_stale_locks(db, max_age_minutes=10)
        assert cleaned == 0
        cursor = await db.execute("SELECT locked_by FROM articles WHERE id = ?", (article_id,))
        row = await cursor.fetchone()
        assert row["locked_by"] == "worker-01"
