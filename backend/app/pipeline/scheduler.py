"""Scheduling: compute next publish slot from user preferences."""

import json
from datetime import datetime, timezone, timedelta, time as dt_time
from zoneinfo import ZoneInfo

import aiosqlite


WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def compute_next_publish_slot(
    publish_days: list[str],
    publish_time: str,
    publish_timezone: str,
    now_utc: datetime | None = None,
    taken_slots: list[str] | None = None,
) -> str:
    """Compute next available publish slot as UTC ISO string.

    publish_days: ["monday", "thursday"]
    publish_time: "09:00" (in the user's timezone)
    publish_timezone: "America/New_York"

    Returns ISO 8601 UTC string (e.g. "2026-03-20T13:00:00Z").
    The publish_time is interpreted in publish_timezone, then converted to UTC.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # Ensure now_utc is timezone-aware
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    taken = set(taken_slots or [])
    hour, minute = map(int, publish_time.split(":"))

    # Resolve user timezone
    try:
        user_tz = ZoneInfo(publish_timezone)
    except (KeyError, Exception):
        user_tz = ZoneInfo("UTC")

    target_days = sorted([WEEKDAY_MAP[d.lower()] for d in publish_days if d.lower() in WEEKDAY_MAP])

    if not target_days:
        target_days = [0, 3]  # Default: Monday, Thursday

    # Minimum 1 hour buffer from now
    min_time = now_utc + timedelta(hours=1)

    # Work in the user's timezone to find candidates
    now_local = now_utc.astimezone(user_tz)

    # Check next 90 days (enough for 8 articles/cycle on 2 publish days/week)
    for day_offset in range(90):
        candidate_date = now_local.date() + timedelta(days=day_offset)
        candidate_weekday = candidate_date.weekday()
        if candidate_weekday in target_days:
            # Build the candidate in the user's timezone
            candidate_local = datetime(
                candidate_date.year, candidate_date.month, candidate_date.day,
                hour, minute, 0, tzinfo=user_tz,
            )
            # Convert to UTC for comparison and storage
            candidate_utc = candidate_local.astimezone(timezone.utc)

            if candidate_utc >= min_time:
                slot_str = candidate_utc.isoformat().replace("+00:00", "Z")
                if slot_str not in taken:
                    return slot_str

    # Fallback: next valid publish day at publish_time in user's tz.
    # This should rarely fire (90 days = ~26 slots per 2-day schedule),
    # but if it does, respect publish_days — never schedule on an off-day.
    for day_offset in range(1, 8):
        fallback_date = now_local.date() + timedelta(days=90 + day_offset)
        if fallback_date.weekday() in target_days:
            fallback_local = datetime(
                fallback_date.year, fallback_date.month, fallback_date.day,
                hour, minute, 0, tzinfo=user_tz,
            )
            fallback_utc = fallback_local.astimezone(timezone.utc)
            return fallback_utc.isoformat().replace("+00:00", "Z")

    # Ultimate fallback: next Monday at publish_time (should never reach here)
    for day_offset in range(1, 8):
        fallback_date = now_local.date() + timedelta(days=day_offset)
        if fallback_date.weekday() == 0:  # Monday
            fallback_local = datetime(
                fallback_date.year, fallback_date.month, fallback_date.day,
                hour, minute, 0, tzinfo=user_tz,
            )
            fallback_utc = fallback_local.astimezone(timezone.utc)
            return fallback_utc.isoformat().replace("+00:00", "Z")


async def get_taken_slots(db: aiosqlite.Connection, user_id: str) -> list[str]:
    """Get already-scheduled publish slots for a user."""
    cursor = await db.execute(
        "SELECT scheduled_publish_at FROM articles WHERE user_id = ? AND scheduled_publish_at IS NOT NULL",
        (user_id,),
    )
    return [row["scheduled_publish_at"] for row in await cursor.fetchall()]
