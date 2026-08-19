"""Cost tracking and ceiling enforcement for articles."""

import json

import aiosqlite

from app.utils.ulid import generate_id
from app.utils.time import utc_now


async def record_llm_cost(
    db: aiosqlite.Connection,
    article_id: str,
    user_id: str,
    event_type: str,
    cost_cents: int,
    payload: dict | None = None,
) -> None:
    """Record an LLM cost event in pipeline_events."""
    event_id = generate_id()
    now = utc_now()
    event_payload = json.dumps({
        "cost_cents": cost_cents,
        **(payload or {}),
    })
    await db.execute(
        """INSERT INTO pipeline_events (id, article_id, user_id, event_type, payload, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (event_id, article_id, user_id, event_type, event_payload, now),
    )
    await db.commit()


async def check_cost_ceiling(
    db: aiosqlite.Connection,
    article_id: str,
    ceiling_cents: int,
) -> dict:
    """Check if article has exceeded cost ceiling.
    
    Returns: { under_ceiling: bool, total_cost_cents: int }
    """
    cursor = await db.execute(
        """SELECT payload FROM pipeline_events
           WHERE article_id = ? AND event_type IN ('llm_call', 'image_generation')""",
        (article_id,),
    )
    rows = await cursor.fetchall()
    total = 0
    for row in rows:
        try:
            data = json.loads(row["payload"])
            total += data.get("cost_cents", 0)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "under_ceiling": total < ceiling_cents,
        "total_cost_cents": total,
    }
