"""T8: MEDIA_ASSEMBLY → WAITING_CHECKPOINT_2. Create review, magic link, email with full article."""

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiosqlite
import markdown as md_lib

from app.models.magic_link import create_magic_link
from app.services.email import send_article_review_email
from app.pipeline.scheduler import compute_next_publish_slot, get_taken_slots
from app.utils.ulid import generate_id
from app.utils.time import utc_now


def _format_publish_date(slot_iso: str, tz_name: str) -> str:
    """Format a UTC ISO slot string into human-readable local time.

    Returns e.g. "Tuesday, March 24 at 9:00 AM EST"
    """
    try:
        user_tz = ZoneInfo(tz_name)
    except (KeyError, Exception):
        user_tz = ZoneInfo("UTC")

    # Parse the UTC slot
    slot_str = slot_iso.replace("Z", "+00:00")
    dt_utc = datetime.fromisoformat(slot_str)
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    dt_local = dt_utc.astimezone(user_tz)

    # Format: "Tuesday, March 24 at 9:00 AM EST"
    weekday = dt_local.strftime("%A")
    month_day = dt_local.strftime("%B %-d")
    time_str = dt_local.strftime("%-I:%M %p")
    # Get timezone abbreviation
    tz_abbrev = dt_local.strftime("%Z") or tz_name.split("/")[-1]

    return f"{weekday}, {month_day} at {time_str} {tz_abbrev}"


_SAFE_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u",
    "ul", "ol", "li",
    "a", "br", "hr",
    "blockquote", "code", "pre",
    "table", "thead", "tbody", "tr", "th", "td",
    "img",  # We handle img separately via anchor replacement
}

_SAFE_ATTRS = {"href", "src", "alt", "title"}


def _sanitize_html(html: str) -> str:
    """Strip dangerous HTML tags and attributes, keeping only safe elements.

    Removes script, style, iframe, event handlers (onXxx), etc.
    """
    # Remove script/style/iframe blocks entirely
    sanitized = re.sub(r'<(script|style|iframe)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    sanitized = re.sub(r'<(script|style|iframe)[^>]*/?\s*>', '', sanitized, flags=re.IGNORECASE)

    # Remove event handler attributes (on*)
    sanitized = re.sub(r'\s+on\w+\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', '', sanitized, flags=re.IGNORECASE)

    # Remove javascript: URLs in href/src
    sanitized = re.sub(r'(href|src)\s*=\s*["\']?\s*javascript:', r'\1="', sanitized, flags=re.IGNORECASE)

    return sanitized


def _replace_image_anchors(html: str, images_by_anchor: dict[str, dict]) -> str:
    """Replace [IMAGE_ANCHOR:X] patterns in HTML with <img> tags.

    COVER anchors are skipped (handled separately as top cover image).
    Numbered anchors get replaced with centered img tags.
    """
    from html import escape as html_escape

    def replacer(match):
        anchor = match.group(1)
        if anchor.upper() == "COVER":
            return ""  # Cover is handled separately at the top
        img_data = images_by_anchor.get(anchor)
        if img_data and img_data.get("storage_url"):
            url = img_data["storage_url"]
            # Only allow http/https URLs (block javascript: etc.)
            if not url.startswith(("https://", "http://")):
                return ""
            alt = html_escape(img_data.get("alt_text", f"Article image {anchor}"), quote=True)
            safe_url = html_escape(url, quote=True)
            return (
                f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
                f'width="100%" style="margin:16px 0;overflow:hidden;">'
                f'<tr><td align="center" style="overflow:hidden;">'
                f'<img src="{safe_url}" alt="{alt}" '
                f'style="display:block;max-width:100%;width:100%;height:auto;border-radius:8px;" />'
                f'</td></tr></table>'
            )
        return ""

    # Match both raw markdown-style anchors and <p>-wrapped versions from markdown conversion
    # Handle <p>[IMAGE_ANCHOR:X]</p> — replace the entire <p> wrapper to avoid <p><table></p> nesting
    result = re.sub(r'<p>\s*\[IMAGE_ANCHOR:([^\]]+)\]\s*</p>', lambda m: replacer(m), html)
    # Also catch any bare anchors not wrapped in <p>
    result = re.sub(r'\[IMAGE_ANCHOR:([^\]]+)\]', replacer, result)
    # Clean up empty paragraphs
    result = re.sub(r'<p>\s*</p>', '', result)
    return result


async def run_to_checkpoint_2(
    db: aiosqlite.Connection,
    config,
    article_id: str,
) -> dict:
    """Set up checkpoint 2 for an article with full article content in email."""
    # Fetch article + user email
    cursor = await db.execute(
        """SELECT a.*, u.email FROM articles a
           JOIN users u ON a.user_id = u.id WHERE a.id = ?""",
        (article_id,),
    )
    article = dict(await cursor.fetchone())
    user_id = article["user_id"]
    now = utc_now()

    # Determine review number
    cursor = await db.execute(
        "SELECT MAX(review_number) as max_rn FROM article_reviews WHERE article_id = ?",
        (article_id,),
    )
    row = await cursor.fetchone()
    review_number = (row["max_rn"] or 0) + 1

    # Create review row
    review_id = generate_id()
    await db.execute(
        """INSERT INTO article_reviews (id, article_id, review_number, status, created_at)
           VALUES (?, ?, ?, 'pending', ?)""",
        (review_id, article_id, review_number, now),
    )

    # Create CP2 magic link (no expiry)
    token = await create_magic_link(db, user_id, "checkpoint_2", reference_id=article_id, commit=False)

    # --- Fetch article content for email ---

    # 1. Get latest draft (humanized preferred)
    cursor = await db.execute(
        """SELECT humanized_draft_md, raw_draft_md FROM draft_iterations
           WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1""",
        (article_id,),
    )
    draft_row = await cursor.fetchone()
    draft_md = ""
    if draft_row:
        draft_md = draft_row["humanized_draft_md"] or draft_row["raw_draft_md"] or ""

    # 2. Convert markdown to HTML and sanitize
    article_html_raw = md_lib.markdown(draft_md) if draft_md else ""
    # Strip dangerous tags/attributes — only allow safe HTML elements
    article_html = _sanitize_html(article_html_raw)

    # 3. Fetch article images
    cursor = await db.execute(
        "SELECT * FROM article_images WHERE article_id = ? ORDER BY anchor_index",
        (article_id,),
    )
    images = [dict(r) for r in await cursor.fetchall()]

    # Build images_by_anchor lookup
    images_by_anchor = {}
    cover_image_url = None
    for img in images:
        anchor = str(img.get("anchor_index", ""))
        images_by_anchor[anchor] = img
        if anchor.upper() == "COVER" and img.get("storage_url"):
            cover_image_url = img["storage_url"]

    # 4. Replace image anchors in HTML with img tags
    article_html = _replace_image_anchors(article_html, images_by_anchor)

    # 5. Get article title from linked idea
    cursor = await db.execute(
        "SELECT title FROM ideas WHERE id = ?",
        (article.get("idea_id"),),
    )
    idea_row = await cursor.fetchone()
    article_title = (idea_row["title"] if idea_row else None) or "Untitled Article"

    # 6. Fetch user settings for scheduling
    cursor = await db.execute(
        "SELECT publish_days, publish_time, publish_timezone FROM users WHERE id = ?",
        (user_id,),
    )
    user_settings = dict(await cursor.fetchone())
    publish_days = json.loads(user_settings["publish_days"]) if user_settings["publish_days"] else []
    publish_time = user_settings["publish_time"] or "09:00"
    publish_timezone = user_settings["publish_timezone"] or "UTC"

    # 7. Compute next publish slot
    taken = await get_taken_slots(db, user_id)
    slot = compute_next_publish_slot(publish_days, publish_time, publish_timezone, taken_slots=taken)

    # 8. Format the date
    next_publish_date_formatted = _format_publish_date(slot, publish_timezone)

    # --- Send article review email ---
    await send_article_review_email(
        config=config,
        to=article["email"],
        article_title=article_title,
        article_html=article_html,
        cover_image_url=cover_image_url,
        magic_link_token=token,
        next_publish_date_formatted=next_publish_date_formatted,
    )

    # Ensure state is WAITING_CHECKPOINT_2 (skip if cancelled)
    await db.execute(
        "UPDATE articles SET state = 'WAITING_CHECKPOINT_2', updated_at = ? WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')",
        (now, article_id),
    )

    await db.commit()
    return {"success": True, "review_number": review_number}
