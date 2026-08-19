"""Shared utilities for pipeline transitions."""

import aiosqlite
import logging

logger = logging.getLogger(__name__)

# Terminal states that must never be overwritten by the worker
TERMINAL_STATES = ("ARCHIVED", "FAILED")


async def safe_state_update(
    db: aiosqlite.Connection,
    article_id: str,
    new_state: str,
    extra_sets: str = "",
    extra_params: tuple = (),
) -> bool:
    """Update article state only if article is not in a terminal state.
    
    Returns True if the update was applied, False if the article was
    cancelled/failed while the worker was processing.
    
    Usage:
        updated = await safe_state_update(db, article_id, "HUMANIZING",
            extra_sets="lifetime_draft_iterations = ?,",
            extra_params=(iteration,))
    """
    from app.utils.time import utc_now
    now = utc_now()
    
    placeholders = ",".join("?" for _ in TERMINAL_STATES)
    
    sql = f"""UPDATE articles SET {extra_sets} state = ?, updated_at = ?
              WHERE id = ? AND state NOT IN ({placeholders})"""
    
    params = (*extra_params, new_state, now, article_id, *TERMINAL_STATES)
    
    cursor = await db.execute(sql, params)
    await db.commit()
    
    if cursor.rowcount == 0:
        logger.warning(
            "safe_state_update_skipped",
            article_id=article_id,
            target_state=new_state,
            reason="article in terminal state (likely cancelled)",
        )
        return False
    
    return True
