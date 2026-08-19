"""Task 1.6: Usage ledger tests."""

import pytest

from app.models.user import create_user
from app.models.usage import (
    get_or_create_current_ledger,
    increment_articles_started,
    get_articles_remaining,
    ArticleLimitError,
)
from app.utils.ulid import generate_id
from app.utils.time import utc_now


async def _insert_counted_article(db, user_id: str) -> str:
    """Insert a minimal article in PUBLISHED state (counts toward effective usage)."""
    batch_id = generate_id()
    seed_id = generate_id()
    idea_id = generate_id()
    article_id = generate_id()
    now = utc_now()
    await db.execute(
        "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'processed', ?)",
        (batch_id, user_id, now),
    )
    await db.execute(
        "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', 'test', ?)",
        (seed_id, batch_id, now),
    )
    await db.execute(
        "INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at) VALUES (?, ?, ?, 'T', 'A', 'kw', 'approved', ?)",
        (idea_id, batch_id, seed_id, now),
    )
    await db.execute(
        "INSERT INTO articles (id, user_id, idea_id, state, created_at, updated_at) VALUES (?, ?, ?, 'PUBLISHED', ?, ?)",
        (article_id, user_id, idea_id, now, now),
    )
    await db.commit()
    return article_id


@pytest.mark.asyncio
async def test_ledger_created(db):
    """get_or_create creates a ledger row."""
    user = await create_user(db, "ledger@test.com")
    await db.execute(
        "UPDATE users SET subscription_status = 'active' WHERE id = ?",
        (user["id"],),
    )
    await db.commit()

    ledger = await get_or_create_current_ledger(db, user["id"])
    assert ledger is not None
    assert ledger["articles_started"] == 0
    assert ledger["user_id"] == user["id"]


@pytest.mark.asyncio
async def test_articles_increment(db):
    """increment_articles_started bumps the count."""
    user = await create_user(db, "inc@test.com")
    await db.execute(
        "UPDATE users SET subscription_status = 'active' WHERE id = ?",
        (user["id"],),
    )
    await db.commit()

    await get_or_create_current_ledger(db, user["id"])
    await increment_articles_started(db, user["id"])

    ledger = await get_or_create_current_ledger(db, user["id"])
    assert ledger["articles_started"] == 1


@pytest.mark.asyncio
async def test_limit_enforced(db):
    """Exceeding article limit raises ArticleLimitError.

    Gate uses actual article states, not stale ledger counter.
    """
    user = await create_user(db, "limit@test.com")
    await db.execute(
        "UPDATE users SET subscription_status = 'active', articles_per_cycle_limit = 2 WHERE id = ?",
        (user["id"],),
    )
    await db.commit()

    await get_or_create_current_ledger(db, user["id"])
    # Create 2 real articles (fills limit)
    await _insert_counted_article(db, user["id"])
    await _insert_counted_article(db, user["id"])

    with pytest.raises(ArticleLimitError):
        await increment_articles_started(db, user["id"])


@pytest.mark.asyncio
async def test_articles_remaining(db):
    """get_articles_remaining returns correct count based on actual article states."""
    user = await create_user(db, "remaining@test.com")
    await db.execute(
        "UPDATE users SET subscription_status = 'active', articles_per_cycle_limit = 8 WHERE id = ?",
        (user["id"],),
    )
    await db.commit()

    await get_or_create_current_ledger(db, user["id"])
    # Create 2 real articles
    await _insert_counted_article(db, user["id"])
    await _insert_counted_article(db, user["id"])

    remaining = await get_articles_remaining(db, user["id"])
    assert remaining == 6


@pytest.mark.asyncio
async def test_no_subscription_error(db):
    """No active subscription → error on ledger creation."""
    user = await create_user(db, "nosub@test.com")
    # subscription_status defaults to 'none'

    with pytest.raises(ValueError, match="subscription"):
        await get_or_create_current_ledger(db, user["id"])
