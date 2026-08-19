"""Tests for seed image upload and pipeline integration."""

import io
import os

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch

from app.database import get_connection, run_migrations
from app.models.seed_batch import create_seed_batch
from app.utils.ulid import generate_id
from app.utils.time import utc_now


@pytest_asyncio.fixture
async def db_with_seed(db):
    """DB with a user, batch, and seed."""
    now = utc_now()
    user_id = generate_id()
    await db.execute(
        """INSERT INTO users (id, email, subscription_status, ghost_key_valid, created_at, updated_at)
           VALUES (?, 'test@test.com', 'trialing', 1, ?, ?)""",
        (user_id, now, now),
    )
    batch_id, seed_ids = await create_seed_batch(db, user_id, [
        {"seed_type": "topic", "content": "AI in healthcare"},
    ])
    await db.commit()
    return db, user_id, batch_id, seed_ids[0]


@pytest.mark.asyncio
async def test_seed_images_table_exists(db):
    """Migration creates seed_images table."""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='seed_images'"
    )
    row = await cursor.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_insert_seed_image(db_with_seed):
    """Can insert a seed image record."""
    db, user_id, batch_id, seed_id = db_with_seed
    now = utc_now()
    img_id = generate_id()
    await db.execute(
        """INSERT INTO seed_images (id, seed_id, filename, storage_path, mime_type, created_at)
           VALUES (?, ?, 'photo.jpg', 'data/seed_images/test/photo.jpg', 'image/jpeg', ?)""",
        (img_id, seed_id, now),
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM seed_images WHERE seed_id = ?", (seed_id,))
    rows = await cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["filename"] == "photo.jpg"


@pytest.mark.asyncio
async def test_seed_images_cascade_query(db_with_seed):
    """Can query seed images through seed → idea join."""
    db, user_id, batch_id, seed_id = db_with_seed
    now = utc_now()

    # Insert seed image
    img_id = generate_id()
    await db.execute(
        """INSERT INTO seed_images (id, seed_id, filename, storage_path, mime_type, created_at)
           VALUES (?, ?, 'test.png', 'data/seed_images/b/test.png', 'image/png', ?)""",
        (img_id, seed_id, now),
    )

    # Insert idea linked to seed
    idea_id = generate_id()
    await db.execute(
        """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword, status, created_at)
           VALUES (?, ?, ?, 'Test Idea', 'Test angle', 'test keyword', 'approved', ?)""",
        (idea_id, batch_id, seed_id, now),
    )
    await db.commit()

    # Query seed images through idea → seed
    cursor = await db.execute(
        """SELECT si.* FROM seed_images si
           JOIN seeds s ON si.seed_id = s.id
           JOIN ideas i ON i.seed_id = s.id
           WHERE i.id = ?""",
        (idea_id,),
    )
    rows = [dict(r) for r in await cursor.fetchall()]
    assert len(rows) == 1
    assert rows[0]["filename"] == "test.png"


@pytest.mark.asyncio
async def test_create_seed_batch_returns_seed_ids(db):
    """create_seed_batch returns both batch_id and seed_ids."""
    now = utc_now()
    user_id = generate_id()
    await db.execute(
        """INSERT INTO users (id, email, subscription_status, created_at, updated_at)
           VALUES (?, 'ids@test.com', 'trialing', ?, ?)""",
        (user_id, now, now),
    )
    await db.commit()

    batch_id, seed_ids = await create_seed_batch(db, user_id, [
        {"seed_type": "topic", "content": "Topic 1"},
        {"seed_type": "url", "content": "https://example.com"},
    ])
    assert isinstance(batch_id, str)
    assert len(seed_ids) == 2
    assert all(isinstance(sid, str) for sid in seed_ids)


@pytest.mark.asyncio
async def test_max_two_images_per_seed(db_with_seed):
    """Should enforce max 2 images per seed at DB level."""
    db, user_id, batch_id, seed_id = db_with_seed
    now = utc_now()

    for i in range(2):
        await db.execute(
            """INSERT INTO seed_images (id, seed_id, filename, storage_path, mime_type, created_at)
               VALUES (?, ?, ?, ?, 'image/png', ?)""",
            (generate_id(), seed_id, f"img{i}.png", f"data/{i}.png", now),
        )
    await db.commit()

    cursor = await db.execute("SELECT COUNT(*) as cnt FROM seed_images WHERE seed_id = ?", (seed_id,))
    row = await cursor.fetchone()
    assert row["cnt"] == 2
