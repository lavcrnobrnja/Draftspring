"""Article routes: list articles, cancel article, list batches."""

from fastapi import APIRouter, Request, HTTPException

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session
from app.utils.ulid import generate_id
from app.utils.time import utc_now

router = APIRouter(prefix="/api", tags=["articles"])


@router.get("/articles")
async def list_articles(request: Request):
    """List all articles for the current user."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            """SELECT a.*, i.title, i.target_keyword
               FROM articles a
               JOIN ideas i ON a.idea_id = i.id
               WHERE a.user_id = ?
               ORDER BY a.created_at DESC""",
            (session["user_id"],),
        )
        rows = [dict(r) for r in await cursor.fetchall()]

        # Get image counts and SEO status, add computed fields for frontend
        for row in rows:
            cursor = await db.execute(
                "SELECT COUNT(*) as cnt FROM article_images WHERE article_id = ?",
                (row["id"],),
            )
            row["image_count"] = (await cursor.fetchone())["cnt"]

            # Count images with valid storage URLs
            cursor = await db.execute(
                """SELECT COUNT(*) as cnt FROM article_images
                   WHERE article_id = ? AND storage_url IS NOT NULL
                   AND storage_url != '' AND storage_url NOT LIKE 'local://%'""",
                (row["id"],),
            )
            row["valid_image_count"] = (await cursor.fetchone())["cnt"]

            # Word count from latest draft
            cursor = await db.execute(
                """SELECT humanized_draft_md, raw_draft_md FROM draft_iterations
                   WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1""",
                (row["id"],),
            )
            draft_row = await cursor.fetchone()
            if draft_row:
                draft_text = (draft_row["humanized_draft_md"] or draft_row["raw_draft_md"] or "")
                row["word_count"] = len(draft_text.split())
            else:
                row["word_count"] = 0

            row["keyword"] = row.get("target_keyword", "")
            seo = row.get("seo_meta")
            row["has_seo"] = seo is not None and seo not in ("", "{}")
            # Parse visible_tags from JSON string to array
            vt = row.get("visible_tags")
            if isinstance(vt, str):
                try:
                    import json
                    row["visible_tags"] = json.loads(vt)
                except (json.JSONDecodeError, TypeError):
                    row["visible_tags"] = []
            row["state_label"] = _state_label(row["state"])
            row["column"] = _state_column(row["state"])

    return {"articles": rows}


@router.get("/batches")
async def list_batches(request: Request):
    """List recent batches for the current user with status info."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            """SELECT sb.id, sb.status, sb.source, sb.created_at, sb.expires_at,
                      COUNT(s.id) as seed_count
               FROM seed_batches sb
               LEFT JOIN seeds s ON s.batch_id = sb.id
               WHERE sb.user_id = ?
               GROUP BY sb.id
               ORDER BY sb.created_at DESC
               LIMIT 10""",
            (session["user_id"],),
        )
        batches = [dict(r) for r in await cursor.fetchall()]

    return {"batches": batches}


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: str, request: Request):
    """Get a single batch with its seeds and idea count."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            """SELECT sb.id, sb.status, sb.created_at, sb.expires_at,
                      COUNT(s.id) as seed_count
               FROM seed_batches sb
               LEFT JOIN seeds s ON s.batch_id = sb.id
               WHERE sb.id = ? AND sb.user_id = ?
               GROUP BY sb.id""",
            (batch_id, session["user_id"]),
        )
        batch = await cursor.fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        batch = dict(batch)

        # Get seeds
        cursor = await db.execute(
            "SELECT id, content, seed_type, created_at FROM seeds WHERE batch_id = ?",
            (batch_id,),
        )
        batch["seeds"] = [dict(r) for r in await cursor.fetchall()]

        # Get idea count
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM ideas WHERE batch_id = ?",
            (batch_id,),
        )
        batch["idea_count"] = (await cursor.fetchone())["cnt"]

    return batch


@router.get("/pending-ideas")
async def list_pending_ideas(request: Request):
    """List pending ideas from all waiting_approval batches, grouped by batch."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            """SELECT i.id, i.title, i.angle, i.target_keyword, i.batch_id, sb.created_at as batch_created_at
               FROM ideas i
               JOIN seed_batches sb ON i.batch_id = sb.id
               WHERE sb.user_id = ? AND sb.status = 'waiting_approval' AND i.status = 'pending'
               ORDER BY sb.created_at DESC, i.created_at ASC""",
            (session["user_id"],),
        )
        ideas = [dict(r) for r in await cursor.fetchall()]

        # Group ideas by batch
        batch_map: dict = {}
        for idea in ideas:
            bid = idea["batch_id"]
            if bid not in batch_map:
                batch_map[bid] = {
                    "batch_id": bid,
                    "batch_created_at": idea["batch_created_at"],
                    "idea_count": 0,
                    "ideas": [],
                }
            batch_map[bid]["idea_count"] += 1
            batch_map[bid]["ideas"].append({
                "id": idea["id"],
                "title": idea["title"],
                "angle": idea["angle"],
                "target_keyword": idea["target_keyword"],
            })

    return {"batches": list(batch_map.values()), "ideas": ideas}


def _state_label(state: str) -> str:
    """Human-friendly state label."""
    labels = {
        "OUTLINING": "Outlining",
        "DRAFTING": "Writing",
        "HUMANIZING": "Humanizing",
        "EDIT_REVIEW": "Editing",
        "MEDIA_ASSEMBLY": "Adding Images",
        "WAITING_CHECKPOINT_2": "Ready for Review",
        "REVISION": "Revising",
        "READY_TO_PUBLISH": "Scheduled",
        "PUBLISHING": "Publishing",
        "PUBLISHED": "Published",
        "FAILED": "Failed",
        "ARCHIVED": "Archived",
    }
    return labels.get(state, state)


def _state_column(state: str) -> str:
    """Map article state to kanban column."""
    column_map = {
        "OUTLINING": "in_production",
        "DRAFTING": "in_production",
        "HUMANIZING": "in_production",
        "EDIT_REVIEW": "in_production",
        "MEDIA_ASSEMBLY": "in_production",
        "WAITING_CHECKPOINT_2": "in_review",
        "REVISION": "in_production",
        "READY_TO_PUBLISH": "scheduled",
        "PUBLISHING": "scheduled",
        "PUBLISHED": "published",
        "FAILED": "in_production",
        "ARCHIVED": "archived",
    }
    return column_map.get(state, "in_production")


@router.post("/articles/{article_id}/cancel")
async def cancel_article(article_id: str, request: Request):
    """Cancel an article: archive it and restore budget."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            "SELECT * FROM articles WHERE id = ? AND user_id = ?",
            (article_id, session["user_id"]),
        )
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")

        article = dict(article)
        if article["state"] == "PUBLISHED":
            raise HTTPException(status_code=400, detail="Cannot cancel a published article")
        if article["state"] == "ARCHIVED":
            raise HTTPException(status_code=400, detail="Article already archived")

        now = utc_now()

        # Release lock if held
        if article["locked_by"]:
            await db.execute(
                "UPDATE articles SET locked_by = NULL, locked_at = NULL WHERE id = ?",
                (article_id,),
            )

        # Transition to ARCHIVED
        await db.execute(
            "UPDATE articles SET state = 'ARCHIVED', updated_at = ? WHERE id = ?",
            (now, article_id),
        )

        # Restore budget: decrement articles_started in current usage ledger
        await db.execute(
            """UPDATE usage_ledger SET articles_started = MAX(0, articles_started - 1), updated_at = ?
               WHERE user_id = ? AND billing_cycle_end > ?""",
            (now, session["user_id"], now),
        )

        # Log event
        event_id = generate_id()
        await db.execute(
            """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, created_at)
               VALUES (?, ?, ?, 'manual_intervention', ?, 'ARCHIVED', ?)""",
            (event_id, article_id, session["user_id"], article["state"], now),
        )

        await db.commit()

    return {"status": "archived", "article_id": article_id}


# States the retry endpoint can safely rewind to. Terminal states and states
# requiring user input (WAITING_CHECKPOINT_2) are excluded — if an article
# failed in one of those, something weird happened; fall back to OUTLINING.
_RETRY_RESUMABLE_STATES = {
    "OUTLINING", "DRAFTING", "HUMANIZING", "EDIT_REVIEW",
    "MEDIA_ASSEMBLY", "REVISION", "PUBLISHING",
}
_RETRY_FALLBACK_STATE = "OUTLINING"


@router.post("/articles/{article_id}/retry")
async def retry_article(article_id: str, request: Request):
    """Retry a FAILED article by resuming from the exact state it failed in.

    Preserves outline, drafts, and images — only re-runs the step that failed.
    Does not re-charge the user's budget; the article was already counted
    against their cycle when created.

    Idempotent: concurrent calls are safe thanks to the CAS guard in the UPDATE.
    """
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            "SELECT * FROM articles WHERE id = ? AND user_id = ?",
            (article_id, session["user_id"]),
        )
        article = await cursor.fetchone()
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")

        article = dict(article)
        if article["state"] != "FAILED":
            raise HTTPException(
                status_code=400,
                detail=f"Only FAILED articles can be retried (current state: {article['state']})",
            )

        # Resume target: the state the article was in when it failed. Worker
        # started recording this in migration 014; older FAILED rows will have
        # NULL and fall back to OUTLINING (full pipeline re-run).
        failed_from = article.get("failed_from_state")
        if failed_from in _RETRY_RESUMABLE_STATES:
            target_state = failed_from
        else:
            target_state = _RETRY_FALLBACK_STATE

        now = utc_now()

        # CAS guard on state='FAILED' ensures two concurrent retry calls only
        # act once. The second call will see state=target_state and noop.
        cursor = await db.execute(
            """UPDATE articles SET
                   state = ?,
                   failure_reason = NULL,
                   failed_at = NULL,
                   locked_by = NULL,
                   locked_at = NULL,
                   updated_at = ?
               WHERE id = ? AND state = 'FAILED'""",
            (target_state, now, article_id),
        )
        if cursor.rowcount == 0:
            # Another request already retried this article; return idempotent
            # success without creating a duplicate pipeline event.
            return {
                "status": "already_retried",
                "article_id": article_id,
                "resumed_from": target_state,
            }

        # Log retry event for audit trail
        await db.execute(
            """INSERT INTO pipeline_events
                   (id, article_id, user_id, event_type, from_state, to_state, payload, created_at)
               VALUES (?, ?, ?, 'retry', 'FAILED', ?, ?, ?)""",
            (
                generate_id(),
                article_id,
                session["user_id"],
                target_state,
                '{"action":"retry","by":"user"}',
                now,
            ),
        )

        await db.commit()

    return {
        "status": "retrying",
        "article_id": article_id,
        "resumed_from": target_state,
    }
