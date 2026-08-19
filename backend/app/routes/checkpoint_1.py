"""Checkpoint 1 routes: idea review, approval, and regeneration."""

import json

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session
from app.middleware.subscription import require_active_subscription
from app.models.usage import ArticleLimitError
from app.pipeline.transitions.t2_idea_approval import approve_ideas
from app.utils.time import utc_now
from app.utils.ulid import generate_id

router = APIRouter(prefix="/api/checkpoints", tags=["checkpoint_1"])


class ApprovedIdea(BaseModel):
    id: str
    title: str | None = None  # Optional edited title


class ApproveRequest(BaseModel):
    batch_id: str
    approved_ideas: list[ApprovedIdea]


@router.get("/ideas/{batch_id}")
async def get_ideas(batch_id: str, request: Request):
    """Get ideas for a batch (CP1 review page)."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Allow full scope or checkpoint_1 scope for this batch
        if session["scope"] == "checkpoint_1" and session.get("scope_ref") != batch_id:
            raise HTTPException(status_code=403, detail="Access denied for this batch")

        # Get batch
        cursor = await db.execute(
            "SELECT * FROM seed_batches WHERE id = ? AND user_id = ?",
            (batch_id, session["user_id"]),
        )
        batch = await cursor.fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        batch = dict(batch)
        read_only = batch["status"] in ("processed", "expired")

        # Get ideas
        cursor = await db.execute(
            "SELECT * FROM ideas WHERE batch_id = ? ORDER BY created_at",
            (batch_id,),
        )
        ideas = [dict(r) for r in await cursor.fetchall()]

        # Check which ideas have seed images
        has_custom_images = False
        for idea in ideas:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM seed_images WHERE seed_id = ?",
                (idea["seed_id"],),
            )
            count = (await cursor.fetchone())[0]
            idea["has_seed_images"] = count > 0
            if count > 0:
                has_custom_images = True

    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "read_only": read_only,
        "ideas": ideas,
        "has_custom_images": has_custom_images,
        "regen_count": batch.get("regen_count", 0) or 0,
        "max_regen": 3,
    }


@router.post("/ideas/approve")
async def approve_ideas_route(body: ApproveRequest, request: Request):
    """Approve/reject ideas and create articles."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        if session["scope"] == "checkpoint_1" and session.get("scope_ref") != body.batch_id:
            raise HTTPException(status_code=403, detail="Access denied for this batch")

        # Subscription check
        cur = await db.execute("SELECT subscription_status FROM users WHERE id = ?", (session["user_id"],))
        u = await cur.fetchone()
        if u:
            require_active_subscription(dict(u))

        # Check batch status
        cursor = await db.execute(
            "SELECT * FROM seed_batches WHERE id = ? AND user_id = ?",
            (body.batch_id, session["user_id"]),
        )
        batch = await cursor.fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        if batch["status"] == "expired":
            raise HTTPException(status_code=400, detail="This batch has expired")
        if batch["status"] == "processed":
            raise HTTPException(status_code=400, detail="This batch has already been processed")
        if batch["status"] in ("pending_ideation", "processing_ideation"):
            raise HTTPException(status_code=400, detail="Ideas are still being generated")

        try:
            result = await approve_ideas(
                db, session["user_id"], body.batch_id,
                [{"id": a.id, "title": a.title} for a in body.approved_ideas],
            )
        except ArticleLimitError as exc:
            # Capacity failure: do not mutate ideas or batch — let user select fewer ideas
            # or try again next cycle. Plain string detail so api.js can toast it.
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return result


MAX_REGEN = 3


class RegenerateRequest(BaseModel):
    batch_id: str
    feedback: str


@router.post("/ideas/regenerate")
async def regenerate_ideas(body: RegenerateRequest, request: Request):
    """Regenerate ideas for a batch with user feedback. Max 3 regenerations."""
    config = request.app.state.config

    feedback = body.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=422, detail="Feedback is required")
    if len(feedback) > 2000:
        raise HTTPException(status_code=422, detail="Feedback too long (max 2000 characters)")

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        if session["scope"] == "checkpoint_1" and session.get("scope_ref") != body.batch_id:
            raise HTTPException(status_code=403, detail="Access denied for this batch")

        # Subscription check
        cur = await db.execute("SELECT subscription_status FROM users WHERE id = ?", (session["user_id"],))
        u = await cur.fetchone()
        if u:
            require_active_subscription(dict(u))

        # Get batch
        cursor = await db.execute(
            "SELECT * FROM seed_batches WHERE id = ? AND user_id = ?",
            (body.batch_id, session["user_id"]),
        )
        batch = await cursor.fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        batch = dict(batch)

        if batch["status"] == "expired":
            raise HTTPException(status_code=400, detail="This batch has expired")
        if batch["status"] == "processed":
            raise HTTPException(status_code=400, detail="This batch has already been processed")
        if batch["status"] in ("pending_ideation", "processing_ideation"):
            raise HTTPException(status_code=400, detail="Ideas are still being generated")

        regen_count = batch.get("regen_count", 0) or 0
        if regen_count >= MAX_REGEN:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_REGEN} regenerations reached")

        now = utc_now()

        # Mark all current pending ideas as rejected
        await db.execute(
            "UPDATE ideas SET status = 'rejected' WHERE batch_id = ? AND status = 'pending'",
            (body.batch_id,),
        )

        # Reset batch to pending_ideation with feedback
        await db.execute(
            """UPDATE seed_batches
               SET status = 'pending_ideation',
                   regen_count = ?,
                   regen_feedback = ?
               WHERE id = ?""",
            (regen_count + 1, feedback, body.batch_id),
        )

        # Log pipeline event (use state_transition type — it IS a state transition)
        event_id = generate_id()
        await db.execute(
            """INSERT INTO pipeline_events (id, batch_id, user_id, event_type, from_state, to_state, payload, created_at)
               VALUES (?, ?, ?, 'state_transition', 'waiting_approval', 'pending_ideation', ?, ?)""",
            (event_id, body.batch_id, session["user_id"],
             json.dumps({"action": "idea_regeneration", "regen_count": regen_count + 1, "feedback": feedback}), now),
        )

        await db.commit()

    return {
        "status": "regenerating",
        "regen_count": regen_count + 1,
        "max_regen": MAX_REGEN,
    }
