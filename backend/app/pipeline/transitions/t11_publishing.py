"""T11: READY_TO_PUBLISH → PUBLISHING → PUBLISHED. Real Ghost publish."""

import json
import logging
import re

import aiosqlite
import markdown as md_lib

from app.llm.base import LLMProvider
from app.services.encryption import decrypt
from app.services.ghost import (
    upload_image_to_ghost,
    create_ghost_post,
    check_duplicate_post,
)
from app.utils.ulid import generate_id
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


async def run_publishing(
    db: aiosqlite.Connection,
    config,
    article_id: str,
    llm: LLMProvider,
) -> dict:
    """Publish an article to Ghost — uploads images, creates post, marks published."""
    cursor = await db.execute(
        """SELECT a.*, u.email, u.ghost_url, u.ghost_admin_api_key_enc, u.ghost_key_valid, u.ghost_author_id
           FROM articles a JOIN users u ON a.user_id = u.id WHERE a.id = ?""",
        (article_id,),
    )
    article = dict(await cursor.fetchone())
    user_id = article["user_id"]
    now = utc_now()

    # Validate Ghost connection — user DB first, then env fallback
    ghost_url = article.get("ghost_url")
    ghost_key_enc = article.get("ghost_admin_api_key_enc")
    ghost_key_valid = article.get("ghost_key_valid")

    if ghost_url and ghost_key_enc and ghost_key_valid:
        ghost_api_key = decrypt(ghost_key_enc)
    elif config.GHOST_URL and config.GHOST_ADMIN_API_KEY:
        ghost_url = config.GHOST_URL
        ghost_api_key = config.GHOST_ADMIN_API_KEY
        logger.info("Using Ghost credentials from environment fallback")
    else:
        raise Exception(
            "Ghost not configured or key invalid. Connect Ghost in Settings before publishing."
        )

    # Transition to PUBLISHING
    if article["state"] == "READY_TO_PUBLISH":
        await db.execute(
            "UPDATE articles SET state = 'PUBLISHING', updated_at = ? WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')",
            (now, article_id),
        )
        event_id = generate_id()
        await db.execute(
            """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, created_at)
               VALUES (?, ?, ?, 'state_transition', 'READY_TO_PUBLISH', 'PUBLISHING', ?)""",
            (event_id, article_id, user_id, now),
        )
        await db.commit()

    # Get latest draft
    cursor = await db.execute(
        """SELECT humanized_draft_md, raw_draft_md FROM draft_iterations
           WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1""",
        (article_id,),
    )
    draft_row = await cursor.fetchone()
    draft_md = (
        draft_row["humanized_draft_md"] or draft_row["raw_draft_md"]
        if draft_row
        else ""
    )

    # Convert Markdown → HTML
    html = md_lib.markdown(draft_md, extensions=["tables", "fenced_code"]) if draft_md else ""

    # Upload article images to Ghost and replace URLs in HTML
    cursor = await db.execute(
        "SELECT * FROM article_images WHERE article_id = ? ORDER BY anchor_index",
        (article_id,),
    )
    images = [dict(r) for r in await cursor.fetchall()]

    for img in images:
        try:
            # Read image file from storage (S3 URL or local path)
            image_url = img.get("storage_url", "")
            image_data = await _fetch_image_bytes(image_url, config)

            if image_data:
                filename = f"{img['id']}.webp"
                ghost_image_url = await upload_image_to_ghost(
                    ghost_url, ghost_api_key, image_data, filename
                )
                await db.execute(
                    "UPDATE article_images SET ghost_image_url = ? WHERE id = ?",
                    (ghost_image_url, img["id"]),
                )
                logger.info(f"Uploaded image {img['id']} to Ghost: {ghost_image_url}")

                # Replace image URL in HTML
                if image_url and ghost_image_url:
                    html = html.replace(image_url, ghost_image_url)
            else:
                logger.warning(f"Could not fetch image {img['id']} from {image_url}")
        except Exception as e:
            logger.error(f"Failed to upload image {img['id']} to Ghost: {e}")
            # Continue — publish without this image rather than failing entirely

    # Get idea title for the post (needed for slug fallback and post creation)
    idea_cursor = await db.execute(
        "SELECT title FROM ideas WHERE id = ?", (article["idea_id"],)
    )
    idea_row = await idea_cursor.fetchone()
    post_title = idea_row["title"] if idea_row else "Untitled"

    # Build SEO metadata
    seo_meta = json.loads(article["seo_meta"]) if article["seo_meta"] else {}
    slug = seo_meta.get("suggested_slug") or _slugify(post_title)

    # Feature image: use the COVER image specifically, fall back to first image
    feature_image = None
    if images:
        # Try to find the COVER-tagged image first
        cursor2 = await db.execute(
            "SELECT ghost_image_url, storage_url FROM article_images WHERE article_id = ? AND anchor_index = 'COVER' LIMIT 1",
            (article_id,),
        )
        cover_img = await cursor2.fetchone()
        if cover_img:
            feature_image = cover_img["ghost_image_url"] or cover_img["storage_url"]
        else:
            # Fall back to first image by creation order
            cursor2 = await db.execute(
                "SELECT ghost_image_url, storage_url FROM article_images WHERE article_id = ? ORDER BY created_at LIMIT 1",
                (article_id,),
            )
            first_img = await cursor2.fetchone()
            if first_img:
                feature_image = first_img["ghost_image_url"] or first_img["storage_url"]

    # ── DEDUP: strip title + cover image from article body ──────────────
    # The markdown draft starts with "# Title\n![cover](url)". Ghost's post.hbs
    # renders the title from post metadata and the feature image separately,
    # so including them in the HTML body causes duplicates.
    # Same pattern used in email.py for CP2 emails.
    html = re.sub(
        r'^\s*<h1[^>]*>.*?</h1>\s*',
        '',
        html,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Strip the COVER image from body (it's already set as feature_image)
    if feature_image:
        _esc = re.escape(feature_image)
        # Try <p><img src="cover..."></p> first (markdown wraps images in <p>)
        html, n = re.subn(
            rf'<p>\s*<img\s[^>]*src="{_esc}"[^>]*/?>\s*</p>',
            '',
            html,
            count=1,
            flags=re.IGNORECASE,
        )
        if n == 0:
            # Bare <img> not wrapped in <p>
            html = re.sub(
                rf'<img\s[^>]*src="{_esc}"[^>]*/?>',
                '',
                html,
                count=1,
                flags=re.IGNORECASE,
            )

    # Wrap HTML in Ghost card markers
    wrapped_html = f"<!--kg-card-begin: html-->{html}<!--kg-card-end: html-->"

    # Check for duplicate (crash recovery)
    existing = await check_duplicate_post(ghost_url, ghost_api_key, slug)
    if existing:
        ghost_post_id = existing["id"]
        ghost_post_url = existing.get("url", f"{ghost_url}/{slug}/")
        logger.info(f"Post already exists on Ghost (crash recovery): {ghost_post_url}")
    else:

        tags = []
        if seo_meta.get("tags"):
            tag_list = seo_meta["tags"]
            if isinstance(tag_list, str):
                tag_list = [t.strip() for t in tag_list.split(",")]
            tags = [{"name": t} for t in tag_list if t]

        post_data = {
            "title": post_title,
            "slug": slug,
            "html": wrapped_html,
            "status": "published",
            "meta_title": seo_meta.get("meta_title", post_title),
            "meta_description": seo_meta.get("meta_description", ""),
            "tags": tags,
        }
        if feature_image:
            post_data["feature_image"] = feature_image

        # Set author if user selected one (otherwise Ghost defaults to Owner)
        ghost_author_id = article.get("ghost_author_id")
        if ghost_author_id:
            post_data["authors"] = [{"id": ghost_author_id}]

        result = await create_ghost_post(ghost_url, ghost_api_key, post_data)
        ghost_post_id = result["id"]
        ghost_post_url = result.get("url", f"{ghost_url}/{slug}/")

    # Update article as published
    now = utc_now()
    await db.execute(
        """UPDATE articles SET state = 'PUBLISHED', ghost_post_id = ?, ghost_post_url = ?,
           published_at = ?, updated_at = ?
           WHERE id = ? AND state NOT IN ('ARCHIVED', 'FAILED')""",
        (ghost_post_id, ghost_post_url, now, now, article_id),
    )

    # Log transition
    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, article_id, user_id, event_type, from_state, to_state, payload, created_at)
           VALUES (?, ?, ?, 'state_transition', 'PUBLISHING', 'PUBLISHED', ?, ?)""",
        (event_id, article_id, user_id,
         json.dumps({"ghost_post_id": ghost_post_id, "ghost_post_url": ghost_post_url}),
         now),
    )

    # Increment articles_published in usage ledger
    from app.models.usage import get_or_create_current_ledger
    try:
        ledger = await get_or_create_current_ledger(db, user_id)
        await db.execute(
            """UPDATE usage_ledger SET articles_published = articles_published + 1, updated_at = ?
               WHERE id = ?""",
            (now, ledger["id"]),
        )
    except ValueError:
        pass  # User subscription may have lapsed, don't block publish

    # Send publish notification email
    from app.services.email import send_publish_notification_email
    idea_cursor = await db.execute("SELECT title FROM ideas WHERE id = ?", (article["idea_id"],))
    idea_row = await idea_cursor.fetchone()
    article_title = idea_row["title"] if idea_row else "Your Article"
    await send_publish_notification_email(config, article["email"], article_title, ghost_post_url)

    await db.commit()
    return {"success": True, "ghost_post_url": ghost_post_url}


async def _fetch_image_bytes(image_url: str, config) -> bytes | None:
    """Fetch image bytes from S3 URL or local path."""
    if not image_url:
        return None

    import httpx

    # S3 or HTTP URL
    if image_url.startswith("http"):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(image_url)
                if resp.status_code == 200:
                    return resp.content
        except Exception as e:
            logger.error(f"Failed to fetch image from URL {image_url}: {e}")
            return None

    # Local file path
    import aiofiles
    try:
        async with aiofiles.open(image_url, "rb") as f:
            return await f.read()
    except FileNotFoundError:
        logger.error(f"Image file not found: {image_url}")
        return None


def _slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:75].strip("-")
