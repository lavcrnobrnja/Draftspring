"""T10: REVISION → DRAFTING. Load revision notes, transition."""

import aiosqlite

from app.llm.base import LLMProvider
from app.utils.ulid import generate_id
from app.utils.time import utc_now


async def run_revision(
    db: aiosqlite.Connection,
    config,
    article_id: str,
    llm: LLMProvider,
) -> dict:
    """Transition article from REVISION to DRAFTING."""
    cursor = await db.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
    article = dict(await cursor.fetchone())
    now = utc_now()

    await db.execute(
        "UPDATE articles SET state = 'DRAFTING', updated_at = ? WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')",
        (now, article_id),
    )

    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, created_at)
           VALUES (?, ?, ?, 'state_transition', 'REVISION', 'DRAFTING', ?)""",
        (event_id, article_id, article["user_id"], now),
    )

    await db.commit()
    return {"success": True}
