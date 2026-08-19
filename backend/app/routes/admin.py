"""Admin routes: overview, users, articles, actions (retry/rollback/archive)."""

from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, EmailStr

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session
from app.models.user import get_user_by_email, create_user
from app.models.magic_link import create_magic_link
from app.services.email import send_magic_link_email
from app.utils.time import utc_now

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def _require_admin(config, db, request: Request) -> dict:
    """Verify the current user is an admin. Returns the session dict."""
    session = await get_current_session(db, request)
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Get user email
    cursor = await db.execute(
        "SELECT email FROM users WHERE id = ?", (session["user_id"],)
    )
    user = await cursor.fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    admin_emails = [
        e.strip().lower()
        for e in (config.ADMIN_EMAILS or "").split(",")
        if e.strip()
    ]
    if user["email"].lower() not in admin_emails:
        raise HTTPException(status_code=403, detail="Admin access required")

    return session


class AdminLoginRequest(BaseModel):
    email: EmailStr


@router.post("/login")
async def admin_login(body: AdminLoginRequest, request: Request):
    """Send an admin magic link. Only works for ADMIN_EMAILS."""
    config = request.app.state.config

    admin_emails = [
        e.strip().lower()
        for e in (config.ADMIN_EMAILS or "").split(",")
        if e.strip()
    ]
    if body.email.lower() not in admin_emails:
        raise HTTPException(status_code=403, detail="Admin access required")

    async with get_connection(config.DATABASE_PATH) as db:
        user = await get_user_by_email(db, body.email)
        if user is None:
            user = await create_user(db, body.email)

        token = await create_magic_link(db, user["id"], "admin")

        sent = await send_magic_link_email(config, body.email, token, "admin")
        if not sent:
            raise HTTPException(status_code=503, detail="Failed to send email")

    result = {"message": "Admin magic link sent"}
    if config.APP_ENV == "development":
        result["dev_verify_url"] = f"{config.APP_BASE_URL}/auth/verify?token={token}"
    return result


@router.get("/check")
async def admin_check(request: Request):
    """Check if the current user is an admin."""
    config = request.app.state.config
    async with get_connection(config.DATABASE_PATH) as db:
        try:
            await _require_admin(config, db, request)
            return {"is_admin": True}
        except HTTPException:
            return {"is_admin": False}


@router.get("/overview")
async def admin_overview(request: Request):
    """Get admin overview stats."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        await _require_admin(config, db, request)

        # Total users
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM users")
        total_users = (await cursor.fetchone())["cnt"]

        # Articles by state
        cursor = await db.execute(
            "SELECT state, COUNT(*) as cnt FROM articles GROUP BY state"
        )
        articles_by_state = {row["state"]: row["cnt"] for row in await cursor.fetchall()}

        # Total articles
        total_articles = sum(articles_by_state.values())

        # Revenue: count active/trialing subscriptions
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE subscription_status IN ('active', 'trialing')"
        )
        active_subscriptions = (await cursor.fetchone())["cnt"]

        # Total estimated revenue (active subs * $9)
        estimated_mrr = active_subscriptions * 900  # cents

        # Total cost
        cursor = await db.execute(
            "SELECT COALESCE(SUM(estimated_cost_cents), 0) as total FROM usage_ledger"
        )
        total_cost_cents = (await cursor.fetchone())["total"]

        # Failed articles count
        failed_count = articles_by_state.get("FAILED", 0)

    return {
        "total_users": total_users,
        "total_articles": total_articles,
        "articles_by_state": articles_by_state,
        "active_subscriptions": active_subscriptions,
        "estimated_mrr_cents": estimated_mrr,
        "total_cost_cents": total_cost_cents,
        "failed_count": failed_count,
    }


@router.get("/users")
async def admin_users(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    search: str = "",
    status: str = "",
):
    """Get paginated, searchable user list."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        await _require_admin(config, db, request)

        conditions = []
        params = []

        if search:
            conditions.append("u.email LIKE ?")
            params.append(f"%{search}%")

        if status:
            conditions.append("u.subscription_status = ?")
            params.append(status)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        offset = (page - 1) * per_page

        # Count total
        cursor = await db.execute(
            f"SELECT COUNT(*) as cnt FROM users u{where}", params
        )
        total = (await cursor.fetchone())["cnt"]

        # Fetch page
        cursor = await db.execute(
            f"""SELECT u.id, u.email, u.subscription_status, u.ghost_key_valid,
                       u.created_at, u.updated_at,
                       (SELECT COUNT(*) FROM articles WHERE user_id = u.id) as article_count,
                       (SELECT COUNT(*) FROM articles WHERE user_id = u.id AND state = 'PUBLISHED') as published_count
                FROM users u{where}
                ORDER BY u.created_at DESC
                LIMIT ? OFFSET ?""",
            params + [per_page, offset],
        )
        users = [dict(r) for r in await cursor.fetchall()]

    return {
        "users": users,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


@router.get("/articles")
async def admin_articles(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    state: str = "",
    user_id: str = "",
):
    """Get paginated, filterable article list."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        await _require_admin(config, db, request)

        conditions = []
        params = []

        if state:
            conditions.append("a.state = ?")
            params.append(state)

        if user_id:
            conditions.append("a.user_id = ?")
            params.append(user_id)

        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        offset = (page - 1) * per_page

        # Count
        cursor = await db.execute(
            f"SELECT COUNT(*) as cnt FROM articles a{where}", params
        )
        total = (await cursor.fetchone())["cnt"]

        # Fetch
        cursor = await db.execute(
            f"""SELECT a.*, i.title, i.target_keyword, u.email as user_email
                FROM articles a
                JOIN ideas i ON a.idea_id = i.id
                JOIN users u ON a.user_id = u.id
                {where}
                ORDER BY a.updated_at DESC
                LIMIT ? OFFSET ?""",
            params + [per_page, offset],
        )
        articles = [dict(r) for r in await cursor.fetchall()]

    return {
        "articles": articles,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


@router.get("/articles/{article_id}")
async def admin_article_detail(article_id: str, request: Request):
    """Get full article detail with pipeline events."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        await _require_admin(config, db, request)

        # Article + idea + user
        cursor = await db.execute(
            """SELECT a.*, i.title, i.angle, i.target_keyword, u.email as user_email
               FROM articles a
               JOIN ideas i ON a.idea_id = i.id
               JOIN users u ON a.user_id = u.id
               WHERE a.id = ?""",
            (article_id,),
        )
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")

        article = dict(article)

        # Draft iterations
        cursor = await db.execute(
            """SELECT id, iteration_number, critique_verdict, created_at
               FROM draft_iterations WHERE article_id = ?
               ORDER BY iteration_number""",
            (article_id,),
        )
        article["draft_iterations"] = [dict(r) for r in await cursor.fetchall()]

        # Article images
        cursor = await db.execute(
            """SELECT id, anchor_index, source_type, alt_text, storage_url, created_at
               FROM article_images WHERE article_id = ?
               ORDER BY anchor_index""",
            (article_id,),
        )
        article["images"] = [dict(r) for r in await cursor.fetchall()]

        # Reviews
        cursor = await db.execute(
            """SELECT * FROM article_reviews WHERE article_id = ?
               ORDER BY review_number""",
            (article_id,),
        )
        article["reviews"] = [dict(r) for r in await cursor.fetchall()]

        # Pipeline events
        cursor = await db.execute(
            """SELECT * FROM pipeline_events WHERE article_id = ?
               ORDER BY created_at""",
            (article_id,),
        )
        article["pipeline_events"] = [dict(r) for r in await cursor.fetchall()]

    return {"article": article}


# ── Valid article states for rollback ──
VALID_STATES = {
    "OUTLINING", "DRAFTING", "HUMANIZING", "EDIT_REVIEW",
    "MEDIA_ASSEMBLY", "WAITING_CHECKPOINT_2", "REVISION",
    "READY_TO_PUBLISH", "PUBLISHING", "PUBLISHED",
    "FAILED", "ARCHIVED",
}

# State to fall back to when retrying a FAILED article
# We go to the earliest safe re-entry point
RETRY_TARGET_STATE = "OUTLINING"


@router.post("/articles/{article_id}/retry")
async def admin_retry_article(article_id: str, request: Request):
    """Reset a FAILED article back to a retryable state."""
    config = request.app.state.config
    now = utc_now()

    async with get_connection(config.DATABASE_PATH) as db:
        await _require_admin(config, db, request)

        cursor = await db.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")

        if article["state"] != "FAILED":
            raise HTTPException(status_code=400, detail="Only FAILED articles can be retried")

        # Reset to OUTLINING (safe re-entry), clear error and reset iterations
        await db.execute(
            """UPDATE articles SET state = ?, failure_reason = NULL, failed_at = NULL,
               locked_by = NULL, locked_at = NULL, lifetime_draft_iterations = 0, updated_at = ?
               WHERE id = ?""",
            (RETRY_TARGET_STATE, now, article_id),
        )

        # Delete existing draft iterations and images to avoid UNIQUE constraint
        # violations when the worker recreates them from scratch
        await db.execute(
            "DELETE FROM draft_iterations WHERE article_id = ?",
            (article_id,),
        )
        await db.execute(
            "DELETE FROM article_images WHERE article_id = ?",
            (article_id,),
        )

        # Get user_id for pipeline event
        user_id = article["user_id"]

        # Log pipeline event
        from app.utils.ulid import generate_id
        await db.execute(
            """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, payload, created_at)
               VALUES (?, ?, ?, 'manual_intervention', ?, ?, '{"action":"retry","by":"admin"}', ?)""",
            (generate_id(), article_id, user_id, "FAILED", RETRY_TARGET_STATE, now),
        )
        await db.commit()

    return {"new_state": RETRY_TARGET_STATE, "failure_cleared": True}


class RollbackRequest(BaseModel):
    target_state: str
    reset_iterations: bool = False


@router.post("/articles/{article_id}/rollback")
async def admin_rollback_article(article_id: str, body: RollbackRequest, request: Request):
    """Rollback an article to any valid state."""
    config = request.app.state.config
    now = utc_now()

    if body.target_state not in VALID_STATES:
        raise HTTPException(status_code=400, detail=f"Invalid target state: {body.target_state}")

    # Prevent dangerous forward jumps to terminal states (PUBLISHED requires pipeline)
    DANGEROUS_TARGETS = {"PUBLISHED", "PUBLISHING"}
    if body.target_state in DANGEROUS_TARGETS:
        raise HTTPException(status_code=400, detail=f"Cannot rollback to {body.target_state}. This state requires the pipeline to complete.")

    async with get_connection(config.DATABASE_PATH) as db:
        await _require_admin(config, db, request)

        cursor = await db.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")

        old_state = article["state"]

        updates = "state = ?, locked_by = NULL, locked_at = NULL, updated_at = ?"
        params = [body.target_state, now]

        if body.reset_iterations:
            updates += ", lifetime_draft_iterations = 0"

        # Clear failure info if rolling back from FAILED
        if old_state == "FAILED":
            updates += ", failure_reason = NULL, failed_at = NULL"

        params.append(article_id)
        await db.execute(f"UPDATE articles SET {updates} WHERE id = ?", params)

        # When resetting iterations, also delete existing draft iterations and
        # article images to avoid UNIQUE constraint violations when the worker
        # recreates them starting from iteration 1.
        if body.reset_iterations:
            await db.execute(
                "DELETE FROM draft_iterations WHERE article_id = ?",
                (article_id,),
            )
            await db.execute(
                "DELETE FROM article_images WHERE article_id = ?",
                (article_id,),
            )

        user_id = article["user_id"]

        # Log pipeline event
        from app.utils.ulid import generate_id
        await db.execute(
            """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, payload, created_at)
               VALUES (?, ?, ?, 'manual_intervention', ?, ?, ?, ?)""",
            (generate_id(), article_id, user_id, old_state, body.target_state,
             f'{{"action":"rollback","reset_iterations":{str(body.reset_iterations).lower()}}}', now),
        )
        await db.commit()

    return {"new_state": body.target_state, "old_state": old_state}


@router.post("/articles/{article_id}/archive")
async def admin_archive_article(article_id: str, request: Request):
    """Archive an article and notify the user."""
    config = request.app.state.config
    now = utc_now()

    async with get_connection(config.DATABASE_PATH) as db:
        await _require_admin(config, db, request)

        cursor = await db.execute(
            """SELECT a.*, i.title, u.email as user_email
               FROM articles a
               JOIN ideas i ON a.idea_id = i.id
               JOIN users u ON a.user_id = u.id
               WHERE a.id = ?""",
            (article_id,),
        )
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")

        if article["state"] == "ARCHIVED":
            raise HTTPException(status_code=400, detail="Article is already archived")

        old_state = article["state"]
        await db.execute(
            """UPDATE articles SET state = 'ARCHIVED', locked_by = NULL, locked_at = NULL,
               updated_at = ? WHERE id = ?""",
            (now, article_id),
        )

        user_id = article["user_id"]

        # Log pipeline event
        from app.utils.ulid import generate_id
        await db.execute(
            """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, payload, created_at)
               VALUES (?, ?, ?, 'manual_intervention', ?, 'ARCHIVED', '{"action":"archive","by":"admin"}', ?)""",
            (generate_id(), article_id, user_id, old_state, now),
        )
        await db.commit()

    # Send notification email to user
    from app.services.email import send_archive_notification_email
    await send_archive_notification_email(config, article["user_email"], article["title"])

    return {"state": "ARCHIVED", "old_state": old_state}
