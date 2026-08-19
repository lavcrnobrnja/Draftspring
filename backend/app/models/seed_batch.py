"""Seed batch and seed CRUD operations."""

import aiosqlite

from app.utils.ulid import generate_id
from app.utils.time import utc_now


async def create_seed_batch(
    db: aiosqlite.Connection,
    user_id: str,
    seeds: list[dict],
    image_style: str | None = None,
    image_substyle: str | None = None,
) -> tuple[str, list[str]]:
    """Create a seed batch with its seeds. Returns (batch_id, [seed_ids])."""
    batch_id = generate_id()
    now = utc_now()

    await db.execute(
        """INSERT INTO seed_batches (id, user_id, status, image_style, image_substyle, created_at)
           VALUES (?, ?, 'pending_ideation', ?, ?, ?)""",
        (batch_id, user_id, image_style, image_substyle, now),
    )

    seed_ids = []
    for seed in seeds:
        seed_id = generate_id()
        seed_ids.append(seed_id)
        await db.execute(
            """INSERT INTO seeds (id, batch_id, seed_type, content, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (seed_id, batch_id, seed["seed_type"], seed["content"], now),
        )

    await db.commit()
    return batch_id, seed_ids


async def get_batch_with_seeds(
    db: aiosqlite.Connection, batch_id: str
) -> dict | None:
    """Get a batch with its seeds."""
    cursor = await db.execute("SELECT * FROM seed_batches WHERE id = ?", (batch_id,))
    batch = await cursor.fetchone()
    if not batch:
        return None
    batch = dict(batch)

    cursor = await db.execute(
        "SELECT * FROM seeds WHERE batch_id = ? ORDER BY created_at", (batch_id,)
    )
    batch["seeds"] = [dict(r) for r in await cursor.fetchall()]
    return batch
