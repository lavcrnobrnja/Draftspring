"""Usage ledger operations."""

from datetime import datetime, timezone, timedelta

import aiosqlite

from app.utils.ulid import generate_id
from app.utils.time import utc_now


class ArticleLimitError(Exception):
    """Raised when article limit for billing cycle is exceeded."""
    pass


# States that count toward effective usage — must match Usage page semantics.
# ARCHIVED/cancelled articles do NOT count.
_COUNTED_STATES = (
    "PUBLISHED",
    "OUTLINING",
    "DRAFTING",
    "HUMANIZING",
    "EDIT_REVIEW",
    "MEDIA_ASSEMBLY",
    "WAITING_CHECKPOINT_2",
    "REVISION",
    "READY_TO_PUBLISH",
    "PUBLISHING",
    "FAILED",
)


def _get_current_cycle_bounds_for_user(
    user_created_at: str | None,
) -> tuple[str, str]:
    """Get current 30-day billing cycle anchored to user signup date."""
    now = datetime.now(timezone.utc)

    # Parse user's created_at to anchor cycles
    anchor = None
    if user_created_at:
        try:
            anchor = datetime.fromisoformat(user_created_at.replace("Z", "+00:00"))
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            anchor = None

    if anchor and anchor <= now:
        # Truncate to seconds for consistent DB string matching
        anchor = anchor.replace(microsecond=0)
        elapsed_days = (now - anchor).days
        cycle_number = elapsed_days // 30
        cycle_start = anchor + timedelta(days=cycle_number * 30)
        cycle_end = cycle_start + timedelta(days=30)
    else:
        # Fallback: 30-day window from today
        cycle_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        cycle_end = cycle_start + timedelta(days=30)

    return (
        cycle_start.isoformat().replace("+00:00", "Z"),
        cycle_end.isoformat().replace("+00:00", "Z"),
    )


async def get_or_create_current_ledger(
    db: aiosqlite.Connection, user_id: str
) -> dict:
    """Get or create the usage ledger for the current billing cycle."""
    # Check subscription status and get created_at for cycle anchoring
    cursor = await db.execute(
        "SELECT subscription_status, created_at FROM users WHERE id = ?", (user_id,)
    )
    row = await cursor.fetchone()
    if not row or row[0] not in ("active", "trialing"):
        raise ValueError(f"User {user_id} does not have an active subscription")

    user_created_at = row[1] if row else None
    cycle_start, cycle_end = _get_current_cycle_bounds_for_user(user_created_at)

    cursor = await db.execute(
        "SELECT * FROM usage_ledger WHERE user_id = ? AND billing_cycle_start = ?",
        (user_id, cycle_start),
    )
    row = await cursor.fetchone()
    if row:
        return dict(row)

    # Create new ledger
    ledger_id = generate_id()
    now = utc_now()
    await db.execute(
        """INSERT INTO usage_ledger (id, user_id, billing_cycle_start, billing_cycle_end, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (ledger_id, user_id, cycle_start, cycle_end, now),
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM usage_ledger WHERE id = ?", (ledger_id,))
    return dict(await cursor.fetchone())


async def _get_effective_usage_in_cycle(
    db: aiosqlite.Connection, user_id: str, cycle_start: str
) -> int:
    """Count articles in counted states created on or after cycle_start.

    This mirrors the Usage page semantics: ARCHIVED articles are excluded.
    Uses actual article states — not the potentially-stale ledger counter.
    """
    placeholders = ",".join("?" for _ in _COUNTED_STATES)
    # Use datetime() for comparison to handle both 'Z' and '.microsZ' formats correctly.
    # SQLite string comparison fails when one timestamp ends in '.microsZ' and the
    # other ends in 'Z' within the same second (period '.' < 'Z' in ASCII).
    cursor = await db.execute(
        f"""SELECT COUNT(*) FROM articles
            WHERE user_id = ?
              AND datetime(created_at) >= datetime(?)
              AND state IN ({placeholders})""",
        (user_id, cycle_start, *_COUNTED_STATES),
    )
    row = await cursor.fetchone()
    return row[0] if row else 0


async def increment_articles_started(
    db: aiosqlite.Connection, user_id: str
) -> dict:
    """Check effective capacity and increment articles_started.

    Raises ArticleLimitError if at limit.

    Capacity check is based on actual article states (matching Usage page
    semantics) so that ARCHIVED articles do not block new approvals.
    The ledger counter is still updated as a side-effect for historical
    tracking, but it is never used as the sole gate.
    """
    ledger = await get_or_create_current_ledger(db, user_id)

    # Get user's limit
    cursor = await db.execute(
        "SELECT articles_per_cycle_limit FROM users WHERE id = ?", (user_id,)
    )
    row = await cursor.fetchone()
    limit = row[0]

    # Gate on effective usage (actual article states), not stale ledger counter.
    effective_used = await _get_effective_usage_in_cycle(
        db, user_id, ledger["billing_cycle_start"]
    )
    if effective_used >= limit:
        raise ArticleLimitError(
            f"Article limit reached ({limit} per cycle)"
        )

    now = utc_now()
    await db.execute(
        """UPDATE usage_ledger SET articles_started = articles_started + 1, updated_at = ?
           WHERE id = ?""",
        (now, ledger["id"]),
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM usage_ledger WHERE id = ?", (ledger["id"],))
    return dict(await cursor.fetchone())


async def get_articles_remaining(
    db: aiosqlite.Connection, user_id: str
) -> int:
    """Get number of articles remaining in current cycle.

    Based on effective usage (actual article states), not stale ledger counter.
    """
    ledger = await get_or_create_current_ledger(db, user_id)

    cursor = await db.execute(
        "SELECT articles_per_cycle_limit FROM users WHERE id = ?", (user_id,)
    )
    row = await cursor.fetchone()
    limit = row[0]

    effective_used = await _get_effective_usage_in_cycle(
        db, user_id, ledger["billing_cycle_start"]
    )
    return max(0, limit - effective_used)
