"""Usage routes: timeline cycle stats."""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session

router = APIRouter(prefix="/api/usage", tags=["usage"])

# States considered "in progress" (between OUTLINING and READY_TO_PUBLISH)
IN_PROGRESS_STATES = (
    "OUTLINING",
    "DRAFTING",
    "HUMANIZING",
    "EDIT_REVIEW",
    "MEDIA_ASSEMBLY",
    "WAITING_CHECKPOINT_2",
    "REVISION",
    "READY_TO_PUBLISH",
    "PUBLISHING",
)


def _parse_dt(s: str | None) -> datetime | None:
    """Parse an ISO datetime string into a timezone-aware datetime."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _month_label(dt: datetime) -> str:
    """Return short month name for a datetime."""
    return dt.strftime("%b")


@router.get("")
async def get_usage(request: Request):
    """Get timeline usage stats for the current billing cycle."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        user_id = session["user_id"]
        now = datetime.now(timezone.utc)

        # Get user info
        cursor = await db.execute(
            "SELECT articles_per_cycle_limit, created_at FROM users WHERE id = ?",
            (user_id,),
        )
        user_row = await cursor.fetchone()
        articles_limit = user_row["articles_per_cycle_limit"] if user_row else 8
        user_created_at = _parse_dt(user_row["created_at"]) if user_row else now

        # Get current cycle from latest usage_ledger
        cursor = await db.execute(
            """SELECT billing_cycle_start, billing_cycle_end
               FROM usage_ledger
               WHERE user_id = ?
               ORDER BY billing_cycle_start DESC LIMIT 1""",
            (user_id,),
        )
        ledger_row = await cursor.fetchone()

        if ledger_row:
            cycle_start = _parse_dt(ledger_row["billing_cycle_start"])
            cycle_end = _parse_dt(ledger_row["billing_cycle_end"])
        else:
            # Compute current 30-day billing cycle anchored to signup date
            if user_created_at and user_created_at <= now:
                # Truncate to seconds for consistent matching with usage_ledger
                anchor = user_created_at.replace(microsecond=0)
                elapsed_days = (now - anchor).days
                cycle_number = elapsed_days // 30
                cycle_start = anchor + timedelta(days=cycle_number * 30)
                cycle_end = cycle_start + timedelta(days=30)
            else:
                cycle_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                cycle_end = cycle_start + timedelta(days=30)

        # Ensure we have valid dates
        if not cycle_start:
            cycle_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if not cycle_end:
            cycle_end = cycle_start + timedelta(days=30)

        days_left = max(0, (cycle_end - now).days)

        # Get articles for current cycle
        cycle_start_iso = cycle_start.isoformat().replace("+00:00", "Z")
        cursor = await db.execute(
            """SELECT a.id, a.state, a.published_at, a.scheduled_publish_at,
                      a.failed_at, a.failure_reason, a.created_at as article_created_at,
                      i.title
               FROM articles a
               JOIN ideas i ON a.idea_id = i.id
               WHERE a.user_id = ?
                 AND a.created_at >= ?
               ORDER BY a.created_at ASC""",
            (user_id, cycle_start_iso),
        )
        article_rows = [dict(r) for r in await cursor.fetchall()]

        # Count current cycle stats
        published = sum(1 for a in article_rows if a["state"] == "PUBLISHED")
        failed = sum(1 for a in article_rows if a["state"] == "FAILED")
        in_progress = sum(1 for a in article_rows if a["state"] in IN_PROGRESS_STATES)
        available = max(0, articles_limit - published - failed - in_progress)

        # Build articles list
        articles = []
        for a in article_rows:
            if a["state"] in ("ARCHIVED",):
                continue
            articles.append({
                "id": a["id"],
                "title": a["title"],
                "state": a["state"],
                "published_at": a["published_at"],
                "scheduled_publish_at": a["scheduled_publish_at"],
                "failed_at": a["failed_at"],
                "failure_reason": a["failure_reason"],
            })

        # Build previous cycles
        # Get all usage_ledger rows for this user, ordered desc
        cursor = await db.execute(
            """SELECT billing_cycle_start, billing_cycle_end
               FROM usage_ledger
               WHERE user_id = ?
               ORDER BY billing_cycle_start DESC""",
            (user_id,),
        )
        all_ledger_rows = [dict(r) for r in await cursor.fetchall()]

        # Get ALL articles for this user (for previous cycle counting)
        cursor = await db.execute(
            """SELECT a.state, a.created_at as article_created_at
               FROM articles a
               WHERE a.user_id = ?
               ORDER BY a.created_at ASC""",
            (user_id,),
        )
        all_articles = [dict(r) for r in await cursor.fetchall()]

        previous_cycles = []

        if len(all_ledger_rows) > 1:
            # Use ledger rows for previous cycles (skip the first which is current)
            for ledger in all_ledger_rows[1:7]:  # Up to 6 previous
                l_start = _parse_dt(ledger["billing_cycle_start"])
                l_end = _parse_dt(ledger["billing_cycle_end"])
                if not l_start or not l_end:
                    continue

                l_start_iso = l_start.isoformat().replace("+00:00", "Z")
                l_end_iso = l_end.isoformat().replace("+00:00", "Z")

                # Count articles in this cycle
                cycle_articles = [
                    a for a in all_articles
                    if a["article_created_at"] >= l_start_iso
                    and a["article_created_at"] < l_end_iso
                ]

                p = sum(1 for a in cycle_articles if a["state"] == "PUBLISHED")
                f = sum(1 for a in cycle_articles if a["state"] == "FAILED")
                ip = sum(1 for a in cycle_articles if a["state"] in IN_PROGRESS_STATES)
                unused = max(0, articles_limit - p - f - ip)

                previous_cycles.append({
                    "label": _month_label(l_start),
                    "start": l_start.strftime("%Y-%m-%d"),
                    "end": l_end.strftime("%Y-%m-%d"),
                    "published": p,
                    "failed": f,
                    "unused": unused,
                    "limit": articles_limit,
                })
        elif all_articles:
            # Estimate cycles from article dates if no ledger history
            # Group articles by 30-day windows from user creation
            if user_created_at:
                window_start = user_created_at
                while window_start < cycle_start:
                    window_end = window_start + timedelta(days=30)
                    if window_end > cycle_start:
                        break

                    w_start_iso = window_start.isoformat().replace("+00:00", "Z")
                    w_end_iso = window_end.isoformat().replace("+00:00", "Z")

                    cycle_articles = [
                        a for a in all_articles
                        if a["article_created_at"] >= w_start_iso
                        and a["article_created_at"] < w_end_iso
                    ]

                    if cycle_articles:
                        p = sum(1 for a in cycle_articles if a["state"] == "PUBLISHED")
                        f = sum(1 for a in cycle_articles if a["state"] == "FAILED")
                        ip = sum(1 for a in cycle_articles if a["state"] in IN_PROGRESS_STATES)
                        unused = max(0, articles_limit - p - f - ip)

                        previous_cycles.append({
                            "label": _month_label(window_start),
                            "start": window_start.strftime("%Y-%m-%d"),
                            "end": window_end.strftime("%Y-%m-%d"),
                            "published": p,
                            "failed": f,
                            "unused": unused,
                            "limit": articles_limit,
                        })

                    window_start = window_end

            # Only keep last 6
            previous_cycles = previous_cycles[-6:]

    return {
        "cycle": {
            "start": cycle_start.isoformat().replace("+00:00", "Z"),
            "end": cycle_end.isoformat().replace("+00:00", "Z"),
            "days_left": days_left,
            "articles_limit": articles_limit,
        },
        "current_cycle": {
            "published": published,
            "failed": failed,
            "in_progress": in_progress,
            "available": available,
        },
        "articles": articles,
        "previous_cycles": previous_cycles,
    }
