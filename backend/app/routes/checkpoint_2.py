"""Checkpoint 2 routes: article review, approve, revise."""

import json
import re
import markdown

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session
from app.middleware.subscription import require_active_subscription
from app.pipeline.scheduler import compute_next_publish_slot, get_taken_slots
from app.services.email import send_magic_link_email
from app.utils.ulid import generate_id
from app.utils.time import utc_now

router = APIRouter(prefix="/api/checkpoints", tags=["checkpoint_2"])


class ApproveArticleRequest(BaseModel):
    article_id: str


class ReviseArticleRequest(BaseModel):
    article_id: str
    revision_notes: str

    @field_validator("revision_notes")
    @classmethod
    def notes_min_length(cls, v):
        if len(v.strip()) < 20:
            raise ValueError("Revision notes must be at least 20 characters")
        return v.strip()


@router.get("/article/{article_id}")
async def get_article_preview(article_id: str, request: Request):
    """Get article preview for CP2 review."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Scope check
        if session["scope"] == "checkpoint_2" and session.get("scope_ref") != article_id:
            raise HTTPException(status_code=403, detail="Access denied for this article")

        cursor = await db.execute(
            "SELECT * FROM articles WHERE id = ? AND user_id = ?",
            (article_id, session["user_id"]),
        )
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        article = dict(article)

        # Get latest draft
        cursor = await db.execute(
            """SELECT humanized_draft_md, raw_draft_md FROM draft_iterations
               WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1""",
            (article_id,),
        )
        draft_row = await cursor.fetchone()
        draft_md = draft_row["humanized_draft_md"] or draft_row["raw_draft_md"] if draft_row else ""

        # Convert to HTML
        draft_html_raw = markdown.markdown(draft_md) if draft_md else ""
        # Sanitize HTML — strip script tags, event handlers, and dangerous elements
        draft_html = re.sub(r'<script[^>]*>.*?</script>', '', draft_html_raw, flags=re.DOTALL | re.IGNORECASE)
        draft_html = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', '', draft_html, flags=re.IGNORECASE)

        # Get images
        cursor = await db.execute(
            "SELECT * FROM article_images WHERE article_id = ? ORDER BY anchor_index",
            (article_id,),
        )
        images = [dict(r) for r in await cursor.fetchall()]

        # Get review history
        cursor = await db.execute(
            "SELECT * FROM article_reviews WHERE article_id = ? ORDER BY review_number",
            (article_id,),
        )
        reviews = [dict(r) for r in await cursor.fetchall()]

        # Budget remaining
        budget_remaining = max(0, 5 - article["lifetime_draft_iterations"])

        raw_seo = json.loads(article["seo_meta"]) if article["seo_meta"] else {}
        # Normalize SEO keys — LLMs used inconsistent field names across articles
        seo = {
            "meta_title": raw_seo.get("meta_title") or raw_seo.get("title") or raw_seo.get("title_tag", ""),
            "meta_description": raw_seo.get("meta_description", ""),
            "focus_keyword": raw_seo.get("focus_keyword") or raw_seo.get("target_keyword") or raw_seo.get("keywords", ""),
            "visible_tags": raw_seo.get("visible_tags") or raw_seo.get("tags", []),
            "secondary_keywords": raw_seo.get("secondary_keywords", []),
        }

        # Word count
        draft_text = draft_md or ""
        word_count = len(draft_text.split())

    return {
        "article_id": article_id,
        "state": article["state"],
        "read_only": article["state"] != "WAITING_CHECKPOINT_2",
        "draft_html": draft_html,
        "images": images,
        "seo": seo,
        "review_history": reviews,
        "budget_remaining": budget_remaining,
        "word_count": word_count,
        "scheduled_publish_at": article.get("scheduled_publish_at"),
    }


@router.post("/article/approve")
async def approve_article(body: ApproveArticleRequest, request: Request):
    """Approve article at CP2 → READY_TO_PUBLISH."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        if session["scope"] == "checkpoint_2" and session.get("scope_ref") != body.article_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Subscription check
        cur = await db.execute("SELECT subscription_status FROM users WHERE id = ?", (session["user_id"],))
        u = await cur.fetchone()
        if u:
            require_active_subscription(dict(u))

        cursor = await db.execute(
            "SELECT * FROM articles WHERE id = ? AND user_id = ?",
            (body.article_id, session["user_id"]),
        )
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        article = dict(article)

        if article["state"] != "WAITING_CHECKPOINT_2":
            raise HTTPException(status_code=400, detail=f"Article not in review state (current: {article['state']})")

        # Update review
        now = utc_now()
        await db.execute(
            """UPDATE article_reviews SET status = 'approved', reviewed_at = ?
               WHERE article_id = ? AND status = 'pending'""",
            (now, body.article_id),
        )

        # Compute publish slot
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],))
        user = dict(await cursor.fetchone())
        publish_days = json.loads(user["publish_days"])
        taken = await get_taken_slots(db, session["user_id"])
        slot = compute_next_publish_slot(publish_days, user["publish_time"], user["publish_timezone"], taken_slots=taken)

        await db.execute(
            "UPDATE articles SET state = 'READY_TO_PUBLISH', scheduled_publish_at = ?, updated_at = ? WHERE id = ?",
            (slot, now, body.article_id),
        )

        event_id = generate_id()
        await db.execute(
            """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, created_at)
               VALUES (?, ?, ?, 'state_transition', 'WAITING_CHECKPOINT_2', 'READY_TO_PUBLISH', ?)""",
            (event_id, body.article_id, session["user_id"], now),
        )

        await db.commit()

    return {"status": "approved", "scheduled_publish_at": slot}


@router.post("/article/revise")
async def revise_article(body: ReviseArticleRequest, request: Request):
    """Request revision at CP2 → REVISION."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        if session["scope"] == "checkpoint_2" and session.get("scope_ref") != body.article_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Subscription check
        cur = await db.execute("SELECT subscription_status FROM users WHERE id = ?", (session["user_id"],))
        u = await cur.fetchone()
        if u:
            require_active_subscription(dict(u))

        cursor = await db.execute(
            "SELECT * FROM articles WHERE id = ? AND user_id = ?",
            (body.article_id, session["user_id"]),
        )
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        article = dict(article)

        if article["state"] != "WAITING_CHECKPOINT_2":
            raise HTTPException(status_code=400, detail=f"Article not in review state")

        if article["lifetime_draft_iterations"] >= 5:
            raise HTTPException(status_code=400, detail="Revision budget exhausted")

        now = utc_now()

        # Update review
        await db.execute(
            """UPDATE article_reviews SET status = 'revision_requested', revision_notes = ?, reviewed_at = ?
               WHERE article_id = ? AND status = 'pending'""",
            (body.revision_notes, now, body.article_id),
        )

        # Transition to REVISION
        await db.execute(
            "UPDATE articles SET state = 'REVISION', updated_at = ? WHERE id = ?",
            (now, body.article_id),
        )

        event_id = generate_id()
        await db.execute(
            """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, created_at)
               VALUES (?, ?, ?, 'state_transition', 'WAITING_CHECKPOINT_2', 'REVISION', ?)""",
            (event_id, body.article_id, session["user_id"], now),
        )

        await db.commit()

    return {"status": "revision_requested"}
