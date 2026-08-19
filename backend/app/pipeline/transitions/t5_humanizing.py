"""T5: HUMANIZING → EDIT_REVIEW. Call humanizer → update draft → transition."""

import logging
import re

logger = logging.getLogger(__name__)

import aiosqlite

from app.llm.base import LLMProvider
from app.utils.ulid import generate_id
from app.utils.time import utc_now


async def run_humanizing(
    db: aiosqlite.Connection,
    config,
    article_id: str,
    llm: LLMProvider,
) -> dict:
    """Run humanizing for an article."""
    cursor = await db.execute(
        """SELECT a.*, i.title AS idea_title, i.target_keyword,
                  u.brand_voice
           FROM articles a
           JOIN ideas i ON a.idea_id = i.id
           JOIN users u ON a.user_id = u.id
           WHERE a.id = ?""",
        (article_id,),
    )
    article = dict(await cursor.fetchone())

    # Get latest draft
    cursor = await db.execute(
        """SELECT * FROM draft_iterations WHERE article_id = ?
           ORDER BY iteration_number DESC LIMIT 1""",
        (article_id,),
    )
    draft_iter = dict(await cursor.fetchone())
    raw_draft = draft_iter["raw_draft_md"]

    # Call humanizer
    humanized = await llm.humanize(
        raw_draft,
        brand_voice=article.get("brand_voice", ""),
        focus_keyword=article.get("target_keyword", ""),
        article_title=article.get("idea_title", ""),
    )

    # Validate: IMAGE_ANCHOR tags preserved (supports COVER and numbered anchors)
    raw_anchors = set(re.findall(r"\[IMAGE_ANCHOR:(?:COVER|\d+)\]", raw_draft))
    humanized_anchors = set(re.findall(r"\[IMAGE_ANCHOR:(?:COVER|\d+)\]", humanized))

    if raw_anchors != humanized_anchors:
        # Fallback: use raw draft
        humanized = raw_draft

    # Update draft iteration
    now = utc_now()
    await db.execute(
        "UPDATE draft_iterations SET humanized_draft_md = ? WHERE id = ?",
        (humanized, draft_iter["id"]),
    )

    # Transition (skip if cancelled)
    cursor = await db.execute(
        "UPDATE articles SET state = 'EDIT_REVIEW', updated_at = ? WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')",
        (now, article_id),
    )
    if cursor.rowcount == 0:
        logger.warning("t5_skipped: article %s in terminal state", article_id)
        await db.commit()
        return {"success": False, "error": "Article cancelled during processing"}

    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, created_at)
           VALUES (?, ?, ?, 'state_transition', 'HUMANIZING', 'EDIT_REVIEW', ?)""",
        (event_id, article_id, article["user_id"], now),
    )

    await db.commit()
    return {"success": True}
