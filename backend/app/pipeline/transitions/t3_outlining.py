"""T3: OUTLINING → DRAFTING. Call Deep Research → store outline → transition."""

import json
import re

import aiosqlite

from app.llm.base import LLMProvider
from app.utils.ulid import generate_id
from app.utils.time import utc_now
import logging
logger = logging.getLogger(__name__)


def _derive_visible_tags(sections: list[dict]) -> list[str]:
    """Derive visible tags from first two section headings.
    
    Extract first 2-3 significant words, title-case them.
    """
    skip_words = {"the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "and", "or", "but", "is", "are"}
    tags = []
    for section in sections[:2]:
        heading = section.get("title", section.get("heading", ""))
        words = [w for w in re.split(r"\W+", heading) if w.lower() not in skip_words and len(w) > 1]
        if words:
            tag = " ".join(words[:3]).title()
            tags.append(tag)
    return tags


async def run_outlining(
    db: aiosqlite.Connection,
    config,
    article_id: str,
    llm: LLMProvider,
) -> dict:
    """Run outlining for an article."""
    # Get article + idea
    cursor = await db.execute(
        """SELECT a.*, i.title, i.target_keyword, i.angle, i.search_intent, u.brand_voice, u.default_word_count
           FROM articles a
           JOIN ideas i ON a.idea_id = i.id
           JOIN users u ON a.user_id = u.id
           WHERE a.id = ?""",
        (article_id,),
    )
    article = await cursor.fetchone()
    if not article:
        return {"success": False, "error": "Article not found"}

    idea = {
        "title": article["title"],
        "target_keyword": article["target_keyword"],
        "angle": article["angle"],
        "search_intent": article["search_intent"] or "",
    }
    blog_context = {"brand_voice": article["brand_voice"]}

    # Parse content_brief if present
    content_brief = None
    if article["content_brief"]:
        try:
            content_brief = json.loads(article["content_brief"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Reduce word count by 10% before passing to LLM — compensates for LLMs
    # consistently writing longer than requested
    adjusted_word_count = int(article["default_word_count"] * 0.9)

    # Call LLM
    outline = await llm.generate_outline(
        idea, blog_context, adjusted_word_count,
        content_brief=content_brief,
    )

    # Store outline and SEO — LLM returns "seo_block", fall back to "seo"
    seo_block = outline.get("seo_block", outline.get("seo", {}))
    seo_meta = json.dumps(seo_block)
    # Prefer LLM-generated tags from seo_block; fall back to deriving from headings
    llm_tags = seo_block.get("visible_tags", [])
    if llm_tags and isinstance(llm_tags, list):
        visible_tags = json.dumps(llm_tags)
    else:
        visible_tags = json.dumps(_derive_visible_tags(outline.get("article_outline", outline.get("sections", []))))
    now = utc_now()

    cursor = await db.execute(
        """UPDATE articles SET outline_json = ?, seo_meta = ?, visible_tags = ?,
           state = 'DRAFTING', updated_at = ?
           WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')""",
        (json.dumps(outline), seo_meta, visible_tags, now, article_id),
    )

    if cursor.rowcount == 0:
        logger.warning("t3_skipped: article %s in terminal state", article_id)
        await db.commit()
        return {"success": False, "error": "Article cancelled during processing"}

    # Log transition
    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, created_at)
           VALUES (?, ?, ?, 'state_transition', 'OUTLINING', 'DRAFTING', ?)""",
        (event_id, article_id, article["user_id"], now),
    )

    await db.commit()
    return {"success": True}
