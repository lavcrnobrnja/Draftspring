"""T4: DRAFTING → HUMANIZING. Call GPT-5.4 → insert draft_iteration → transition."""

import json
import logging
logger = logging.getLogger(__name__)

import aiosqlite

from app.llm.base import LLMProvider
from app.utils.ulid import generate_id
from app.utils.time import utc_now


async def run_drafting(
    db: aiosqlite.Connection,
    config,
    article_id: str,
    llm: LLMProvider,
) -> dict:
    """Run drafting for an article."""
    cursor = await db.execute(
        """SELECT a.*, i.title AS idea_title, i.target_keyword,
                  u.brand_voice, u.default_word_count
           FROM articles a
           JOIN ideas i ON a.idea_id = i.id
           JOIN users u ON a.user_id = u.id
           WHERE a.id = ?""",
        (article_id,),
    )
    article = dict(await cursor.fetchone())

    # Check iteration cap
    if article["lifetime_draft_iterations"] >= 5:
        await _transition(db, article_id, article["user_id"], "DRAFTING", "FAILED")
        return {"success": False, "error": "Draft iteration cap reached"}

    # Get outline, SEO, and content brief
    outline = json.loads(article["outline_json"]) if article["outline_json"] else {}
    seo_meta = json.loads(article["seo_meta"]) if article["seo_meta"] else {}
    content_brief = None
    if article.get("content_brief"):
        try:
            content_brief = json.loads(article["content_brief"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Get previous critique (if any) — includes score for revision block
    cursor = await db.execute(
        """SELECT critique_json FROM draft_iterations
           WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1""",
        (article_id,),
    )
    row = await cursor.fetchone()
    previous_critique = json.loads(row["critique_json"]) if row and row["critique_json"] else None
    previous_score = previous_critique.get("overall_score", previous_critique.get("score")) if previous_critique else None

    # Get revision notes (if coming from REVISION state)
    cursor = await db.execute(
        """SELECT revision_notes FROM article_reviews
           WHERE article_id = ? AND status = 'revision_requested'
           ORDER BY review_number DESC LIMIT 1""",
        (article_id,),
    )
    row = await cursor.fetchone()
    user_revision_notes = row["revision_notes"] if row else None

    # Get target word count from outline (already -10% from T3) or user default with -10% adjustment
    outline_wc = outline.get("target_word_count")
    if outline_wc:
        target_word_count = outline_wc
    else:
        # Fallback: apply -10% to compensate for LLMs writing long
        target_word_count = int(article.get("default_word_count", 1500) * 0.9)

    # Extract article title and focus keyword
    article_title = outline.get("working_title") or article.get("idea_title") or "Untitled"
    focus_keyword = seo_meta.get("focus_keyword") or article.get("target_keyword") or ""

    # Call LLM
    draft_md = await llm.draft_article(
        outline=outline,
        seo_meta=seo_meta,
        brand_voice=article["brand_voice"],
        focus_keyword=focus_keyword,
        article_title=article_title,
        previous_critique=previous_critique,
        previous_score=previous_score,
        user_revision_notes=user_revision_notes,
        target_word_count=target_word_count,
        iteration_number=article["lifetime_draft_iterations"] + 1,
        content_brief=content_brief,
    )

    # Increment iteration
    new_iteration = article["lifetime_draft_iterations"] + 1
    now = utc_now()

    cursor = await db.execute(
        "UPDATE articles SET lifetime_draft_iterations = ?, state = 'HUMANIZING', updated_at = ? WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')",
        (new_iteration, now, article_id),
    )
    if cursor.rowcount == 0:
        logger.warning("t4_skipped: article %s in terminal state", article_id)
        await db.commit()
        return {"success": False, "error": "Article cancelled during processing"}

    # Upsert draft iteration (DELETE + INSERT to handle retries)
    await db.execute(
        "DELETE FROM draft_iterations WHERE article_id = ? AND iteration_number = ?",
        (article_id, new_iteration),
    )
    iter_id = generate_id()
    await db.execute(
        """INSERT INTO draft_iterations (id, article_id, iteration_number, raw_draft_md, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (iter_id, article_id, new_iteration, draft_md, now),
    )

    # Log transition
    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, created_at)
           VALUES (?, ?, ?, 'state_transition', 'DRAFTING', 'HUMANIZING', ?)""",
        (event_id, article_id, article["user_id"], now),
    )

    await db.commit()
    return {"success": True, "iteration": new_iteration}


async def _transition(db, article_id, user_id, from_state, to_state):
    now = utc_now()
    await db.execute(
        "UPDATE articles SET state = ?, updated_at = ? WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')",
        (to_state, now, article_id),
    )
    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, created_at)
           VALUES (?, ?, ?, 'state_transition', ?, ?, ?)""",
        (event_id, article_id, user_id, from_state, to_state, now),
    )
    await db.commit()
