"""Task 1.1: Database schema tests."""

import aiosqlite
import pytest


EXPECTED_TABLES = [
    "users", "sessions", "magic_links", "seed_batches", "seeds",
    "ideas", "articles", "draft_iterations", "article_images",
    "vault_images", "article_reviews", "pipeline_events", "usage_ledger",
]

EXPECTED_INDEXES = [
    "idx_articles_user_state",
    "idx_articles_scheduled",
    "idx_articles_locked",
    "idx_ideas_batch_status",
    "idx_magic_links_token",
    "idx_pipeline_events_article",
    "idx_usage_ledger_user_cycle",
    "idx_sessions_user",
    "idx_seed_batches_user",
    "idx_vault_images_user",
]


@pytest.mark.asyncio
async def test_all_tables_exist(db):
    """All 13 tables (11 data + _migrations + sqlite internals) exist after migration."""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables = {row[0] for row in await cursor.fetchall()}
    for table in EXPECTED_TABLES:
        assert table in tables, f"Table {table} missing"
    assert "_migrations" in tables, "_migrations table missing"


@pytest.mark.asyncio
async def test_foreign_keys_enforced(db):
    """FK enforcement: inserting a magic_link with a bad user_id fails."""
    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """INSERT INTO magic_links (id, user_id, token_hash, purpose, created_at)
               VALUES ('ml1', 'nonexistent_user', 'hash123', 'login', '2026-01-01T00:00:00Z')"""
        )


@pytest.mark.asyncio
async def test_unique_constraints(db):
    """Duplicate email on users table raises IntegrityError."""
    now = "2026-01-01T00:00:00Z"
    await db.execute(
        "INSERT INTO users (id, email, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("u1", "test@test.com", now, now),
    )
    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            "INSERT INTO users (id, email, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("u2", "test@test.com", now, now),
        )


@pytest.mark.asyncio
async def test_check_constraints(db):
    """Invalid article state is rejected by check constraint."""
    now = "2026-01-01T00:00:00Z"
    # Create user and idea prerequisites
    await db.execute(
        "INSERT INTO users (id, email, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("u1", "test@test.com", now, now),
    )
    await db.execute(
        "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("sb1", "u1", "pending_ideation", now),
    )
    await db.execute(
        "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, ?, ?, ?)",
        ("s1", "sb1", "topic", "test seed", now),
    )
    await db.execute(
        "INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("i1", "sb1", "s1", "Test Idea", "An angle", "keyword", now),
    )
    with pytest.raises(aiosqlite.IntegrityError):
        await db.execute(
            """INSERT INTO articles (id, user_id, idea_id, state, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("a1", "u1", "i1", "INVALID_STATE", now, now),
        )


@pytest.mark.asyncio
async def test_default_values(db):
    """New user gets correct default values."""
    now = "2026-01-01T00:00:00Z"
    await db.execute(
        "INSERT INTO users (id, email, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("u1", "test@test.com", now, now),
    )
    cursor = await db.execute("SELECT * FROM users WHERE id = 'u1'")
    row = await cursor.fetchone()
    cols = [desc[0] for desc in cursor.description]
    user = dict(zip(cols, row))
    assert user["subscription_status"] == "none"
    assert user["publish_days"] == '[]'
    assert user["publish_time"] == "09:00"
    assert user["publish_timezone"] == "America/New_York"
    assert user["articles_per_cycle_limit"] == 8
    assert user["default_word_count"] == 1500
    assert user["email_bounce"] == 0
    assert user["ghost_key_valid"] == 0


@pytest.mark.asyncio
async def test_all_indexes_exist(db):
    """All expected indexes exist."""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    )
    indexes = {row[0] for row in await cursor.fetchall()}
    for idx in EXPECTED_INDEXES:
        assert idx in indexes, f"Index {idx} missing"


@pytest.mark.asyncio
async def test_wal_mode(db):
    """WAL mode is enabled."""
    cursor = await db.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row[0] == "wal"


@pytest.mark.asyncio
async def test_migration_idempotent(db):
    """Running migration twice does not error."""
    from app.database import run_migrations
    # Already ran once via fixture, run again
    await run_migrations(db)
    # Should still have all tables
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    tables = {row[0] for row in await cursor.fetchall()}
    for table in EXPECTED_TABLES:
        assert table in tables
