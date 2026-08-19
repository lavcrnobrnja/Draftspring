"""Article locking for worker concurrency control."""

from datetime import datetime, timezone, timedelta

import aiosqlite

from app.utils.time import utc_now


async def acquire_lock(
    db: aiosqlite.Connection, article_id: str, worker_id: str
) -> bool:
    """Atomically acquire a lock on an article. Returns True if acquired."""
    now = utc_now()
    # Try to lock: only succeeds if unlocked OR already owned by this worker
    cursor = await db.execute(
        """UPDATE articles SET locked_by = ?, locked_at = ?
           WHERE id = ? AND (locked_by IS NULL OR locked_by = ?)""",
        (worker_id, now, article_id, worker_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def release_lock(
    db: aiosqlite.Connection, article_id: str, worker_id: str
) -> None:
    """Release a lock. Only the owning worker can release."""
    await db.execute(
        "UPDATE articles SET locked_by = NULL, locked_at = NULL WHERE id = ? AND locked_by = ?",
        (article_id, worker_id),
    )
    await db.commit()


async def cleanup_stale_locks(
    db: aiosqlite.Connection, max_age_minutes: int = 10
) -> int:
    """Release locks older than max_age_minutes. Returns count cleaned."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)).isoformat()
    cursor = await db.execute(
        "UPDATE articles SET locked_by = NULL, locked_at = NULL WHERE locked_by IS NOT NULL AND locked_at < ?",
        (cutoff,),
    )
    await db.commit()
    return cursor.rowcount
