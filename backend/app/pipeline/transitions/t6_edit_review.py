"""T6: EDIT_REVIEW → MEDIA_ASSEMBLY or DRAFTING. Critique → score-based decision."""

import json
import logging
logger = logging.getLogger(__name__)

import aiosqlite

from app.llm.base import LLMProvider
from app.utils.ulid import generate_id
from app.utils.time import utc_now


async def run_edit_review(
    db: aiosqlite.Connection,
    config,
    article_id: str,
    llm: LLMProvider,
) -> dict:
    """Run edit review (critique) for an article."""
    cursor = await db.execute(
        """SELECT a.*, i.title AS idea_title, i.angle AS idea_angle,
                  i.target_keyword AS idea_keyword, i.search_intent AS idea_search_intent,
                  u.brand_voice AS user_brand_voice
           FROM articles a
           JOIN ideas i ON a.idea_id = i.id
           JOIN users u ON a.user_id = u.id
           WHERE a.id = ?""",
        (article_id,),
    )
    article = dict(await cursor.fetchone())

    outline = json.loads(article["outline_json"]) if article["outline_json"] else {}
    seo_meta = json.loads(article["seo_meta"]) if article["seo_meta"] else {}

    # Extract article context for the critique prompt
    article_title = outline.get("working_title") or article.get("idea_title") or "Untitled"
    article_angle = article.get("idea_angle") or ""
    search_intent = article.get("idea_search_intent") or ""
    focus_keyword = article.get("idea_keyword") or seo_meta.get("focus_keyword", "")
    brand_voice = article.get("user_brand_voice")
    # Use outline's target (already -10% from T3) or fallback with -10% adjustment
    outline_wc = outline.get("target_word_count")
    target_word_count = outline_wc if outline_wc else int((article.get("default_word_count") or 1500) * 0.9)
    meta_description = seo_meta.get("meta_description", "")

    # Get latest draft
    cursor = await db.execute(
        """SELECT * FROM draft_iterations WHERE article_id = ?
           ORDER BY iteration_number DESC LIMIT 1""",
        (article_id,),
    )
    draft_iter = dict(await cursor.fetchone())
    humanized = draft_iter["humanized_draft_md"] or draft_iter["raw_draft_md"]

    # Get previous critique
    cursor = await db.execute(
        """SELECT critique_json FROM draft_iterations
           WHERE article_id = ? AND critique_json IS NOT NULL
           ORDER BY iteration_number DESC LIMIT 1""",
        (article_id,),
    )
    row = await cursor.fetchone()
    previous_critique = json.loads(row["critique_json"]) if row and row["critique_json"] else None

    # Extract user context from content_brief if present
    user_description = None
    user_keywords = None
    if article.get("content_brief"):
        try:
            cb = json.loads(article["content_brief"])
            user_description = cb.get("user_description")
            user_keywords = cb.get("user_keywords")
        except (json.JSONDecodeError, TypeError):
            pass

    # Call critique
    critique = await llm.critique_draft(
        humanized, outline, seo_meta,
        iteration_number=draft_iter["iteration_number"],
        max_iterations=5,
        previous_critique=previous_critique,
        article_title=article_title,
        article_angle=article_angle,
        search_intent=search_intent,
        focus_keyword=focus_keyword,
        brand_voice=brand_voice,
        target_word_count=target_word_count,
        meta_description=meta_description,
        user_description=user_description,
        user_keywords=user_keywords,
    )

    # Normalize verdict to match DB constraint: 'approved' or 'revision_needed'
    # LLM may return "verdict" or "decision"
    raw_verdict = critique.get("verdict", critique.get("decision", "revision_needed"))
    raw_verdict = str(raw_verdict).lower().strip()
    if raw_verdict in ("approved", "approve", "pass", "accept"):
        normalized_verdict = "approved"
    else:
        normalized_verdict = "revision_needed"
    critique["verdict"] = normalized_verdict

    # Store critique
    await db.execute(
        "UPDATE draft_iterations SET critique_json = ?, critique_verdict = ? WHERE id = ?",
        (json.dumps(critique), normalized_verdict, draft_iter["id"]),
    )

    # SOFTWARE OVERRIDE: score >= 7 → always approve regardless of verdict
    # LLM may return "score" or "overall_score"
    score = critique.get("overall_score", critique.get("score", 0))
    iteration = draft_iter["iteration_number"]
    # Also force-approve at max iterations (3) to prevent infinite loops
    if score >= 7 or iteration >= 3:
        effective_verdict = "approved"
    else:
        effective_verdict = normalized_verdict

    # Apply meta description fix if needed — only if suggestion looks like a real
    # meta description (not LLM review commentary). Must be 50-200 chars and not
    # contain review language like "consider", "strong", "within limits".
    if critique.get("seo_check", {}).get("meta_fix_suggestion"):
        fix = critique["seo_check"]["meta_fix_suggestion"]
        review_markers = ["consider ", "strong", "within ", "character limit", "could be", "suggestion"]
        is_commentary = any(m in fix.lower() for m in review_markers) if fix else True
        if fix and seo_meta and 50 <= len(fix) <= 200 and not is_commentary:
            seo_meta["meta_description"] = fix
            await db.execute(
                "UPDATE articles SET seo_meta = ? WHERE id = ?",
                (json.dumps(seo_meta), article_id),
            )

    now = utc_now()

    # Check if this article is coming from a user revision (CP2 feedback).
    # If revision notes mention images, route through MEDIA_ASSEMBLY to regenerate.
    # If revision notes are text-only, skip MEDIA_ASSEMBLY and preserve existing images.
    cursor = await db.execute(
        """SELECT revision_notes FROM article_reviews
           WHERE article_id = ? AND status = 'revision_requested'
           ORDER BY review_number DESC LIMIT 1""",
        (article_id,),
    )
    revision_row = await cursor.fetchone()
    is_revision = revision_row is not None

    # Determine if user wants image changes
    revision_wants_new_images = False
    if is_revision and revision_row["revision_notes"]:
        _notes_lower = revision_row["revision_notes"].lower()
        _image_keywords = (
            "image", "images", "photo", "photos", "photograph", "picture",
            "pictures", "illustration", "illustrations", "visual", "visuals",
            "graphic", "graphics", "thumbnail", "cover image", "cover photo",
            "artwork", "banner", "img",
        )
        revision_wants_new_images = any(kw in _notes_lower for kw in _image_keywords)

    if effective_verdict == "approved":
        if is_revision and not revision_wants_new_images:
            next_state = "WAITING_CHECKPOINT_2"
        else:
            next_state = "MEDIA_ASSEMBLY"
    elif article["lifetime_draft_iterations"] >= 5:
        # Force-advance at cap
        if is_revision and not revision_wants_new_images:
            next_state = "WAITING_CHECKPOINT_2"
        else:
            next_state = "MEDIA_ASSEMBLY"
    else:
        next_state = "DRAFTING"

    cursor = await db.execute(
        "UPDATE articles SET state = ?, updated_at = ? WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')",
        (next_state, now, article_id),
    )
    if cursor.rowcount == 0:
        logger.warning("t6_skipped: article %s in terminal state", article_id)
        await db.commit()
        return {"success": False, "error": "Article cancelled during processing"}

    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, payload, created_at)
           VALUES (?, ?, ?, 'state_transition', 'EDIT_REVIEW', ?, ?, ?)""",
        (event_id, article_id, article["user_id"], next_state,
         json.dumps({"score": score, "verdict": critique["verdict"], "effective_verdict": effective_verdict}),
         now),
    )

    await db.commit()

    # If skipping media assembly (revision path), re-anchor existing images
    # into the new draft, then run t8 to send the CP2 email.
    if next_state == "WAITING_CHECKPOINT_2":
        # Get the latest draft
        cursor = await db.execute(
            """SELECT id, humanized_draft_md, raw_draft_md FROM draft_iterations
               WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1""",
            (article_id,),
        )
        _di = dict(await cursor.fetchone())
        _draft = _di["humanized_draft_md"] or _di["raw_draft_md"] or ""

        # Get existing images
        cursor = await db.execute(
            "SELECT anchor_index, storage_url, alt_text FROM article_images WHERE article_id = ?",
            (article_id,),
        )
        _images = {row["anchor_index"]: row for row in await cursor.fetchall()}

        # Replace IMAGE_ANCHOR tags with existing image markdown
        for anchor_id, img in _images.items():
            tag = f"[IMAGE_ANCHOR:{anchor_id}]"
            url = img["storage_url"] or "placeholder.png"
            alt = img["alt_text"] or ""
            _draft = _draft.replace(tag, f"![{alt}]({url})")

        await db.execute(
            "UPDATE draft_iterations SET humanized_draft_md = ? WHERE id = ?",
            (_draft, _di["id"]),
        )
        await db.commit()

        from app.pipeline.transitions.t8_to_checkpoint_2 import run_to_checkpoint_2
        await run_to_checkpoint_2(db, config, article_id)

    return {"success": True, "verdict": effective_verdict, "score": score}
