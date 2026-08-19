"""T2: Checkpoint 1 → Article Creation. Approve ideas, create articles."""

import json

import aiosqlite

from app.models.usage import get_or_create_current_ledger, get_articles_remaining, ArticleLimitError
from app.utils.ulid import generate_id
from app.utils.time import utc_now


async def _assemble_content_brief(
    db: aiosqlite.Connection,
    batch_id: str,
    idea_id: str,
) -> dict | None:
    """Assemble content_brief JSON from batch seeds and images.
    
    Returns dict with: user_description, reference_materials, user_keywords, user_images
    Or None if no meaningful brief data exists.
    """
    # Get batch-level metadata and seeds for this batch
    cursor = await db.execute(
        "SELECT image_style, image_substyle FROM seed_batches WHERE id = ?", (batch_id,)
    )
    batch = await cursor.fetchone()

    cursor = await db.execute(
        "SELECT * FROM seeds WHERE batch_id = ? ORDER BY created_at", (batch_id,)
    )
    seeds = [dict(r) for r in await cursor.fetchall()]
    if not seeds:
        return None

    brief = {}
    
    # Extract description and keywords from the primary topic seed
    for seed in seeds:
        if seed["seed_type"] == "topic":
            content = seed["content"]
            if "\n\nKeywords:" in content:
                parts = content.split("\n\nKeywords:", 1)
                brief["user_description"] = parts[0].strip()
                brief["user_keywords"] = parts[1].strip()
            else:
                brief["user_description"] = content.strip()
            break

    if not brief.get("user_description"):
        return None

    if batch and batch["image_style"] and batch["image_substyle"]:
        brief["image_style"] = batch["image_style"]
        brief["image_substyle"] = batch["image_substyle"]

    # Reference materials from URL seeds
    reference_materials = []
    for seed in seeds:
        if seed["seed_type"] == "url":
            ref = {"url": seed["content"]}
            if seed.get("extracted_content"):
                ref["extracted_content"] = seed["extracted_content"]
            reference_materials.append(ref)
    if reference_materials:
        brief["reference_materials"] = reference_materials

    # Get the seed_id for the idea to find relevant images
    cursor = await db.execute("SELECT seed_id FROM ideas WHERE id = ?", (idea_id,))
    idea_row = await cursor.fetchone()
    
    # Collect all seed images from the batch
    user_images = []
    for seed in seeds:
        cursor = await db.execute(
            "SELECT image_role, storage_path, description FROM seed_images WHERE seed_id = ?",
            (seed["id"],),
        )
        for img in await cursor.fetchall():
            img_entry = {
                "role": img["image_role"] or "body",
                "storage_url": img["storage_path"],
            }
            if img["description"]:
                img_entry["description"] = img["description"]
            user_images.append(img_entry)
    if user_images:
        brief["user_images"] = user_images

    return brief


async def approve_ideas(
    db: aiosqlite.Connection,
    user_id: str,
    batch_id: str,
    approved_ideas: list[dict],
) -> dict:
    """Approve selected ideas and create articles.
    
    approved_ideas: [{ id: str, title: str|None }]
    Returns: { articles_created: int, budget_limited: bool }
    """
    now = utc_now()
    approved_ids = {a["id"] for a in approved_ideas}
    title_overrides = {a["id"]: a.get("title") for a in approved_ideas if a.get("title")}

    # Get all ideas for this batch
    cursor = await db.execute(
        "SELECT id FROM ideas WHERE batch_id = ? AND status = 'pending'",
        (batch_id,),
    )
    all_ideas = [dict(r) for r in await cursor.fetchall()]

    # Pre-check capacity before mutating ideas or batch.
    # Reject the whole approval if the user selected more ideas than available slots:
    # no partial creation, no burned/rejected ideas, no processed batch.
    remaining = await get_articles_remaining(db, user_id)
    requested = len(approved_ideas)
    if requested > remaining:
        article_word = "article" if remaining == 1 else "articles"
        raise ArticleLimitError(
            f"You have {remaining} {article_word} remaining this cycle. No articles were created."
        )

    # Update titles if edited
    for idea_id, new_title in title_overrides.items():
        await db.execute(
            "UPDATE ideas SET title = ? WHERE id = ?",
            (new_title, idea_id),
        )

    # Reject non-approved ideas
    for idea in all_ideas:
        if idea["id"] not in approved_ids:
            await db.execute(
                "UPDATE ideas SET status = 'rejected' WHERE id = ?",
                (idea["id"],),
            )

    # Create articles for approved ideas. Capacity was pre-checked above; update
    # the ledger once at the end so this transition has one commit and cannot
    # leave a partially-counted approval batch on cap errors.
    articles_created = 0

    for approved in approved_ideas:
        idea_id = approved["id"]

        # Mark idea as approved
        await db.execute(
            "UPDATE ideas SET status = 'approved', approved_at = ? WHERE id = ?",
            (now, idea_id),
        )

        # Create article in OUTLINING state
        article_id = generate_id()
        await db.execute(
            """INSERT INTO articles (id, user_id, idea_id, state, created_at, updated_at)
               VALUES (?, ?, ?, 'OUTLINING', ?, ?)""",
            (article_id, user_id, idea_id, now, now),
        )

        # Assemble content_brief JSON from batch data
        content_brief = await _assemble_content_brief(db, batch_id, idea_id)
        if content_brief:
            await db.execute(
                "UPDATE articles SET content_brief = ? WHERE id = ?",
                (json.dumps(content_brief), article_id),
            )

        articles_created += 1

    # Count the successfully-created articles in the historical ledger.
    # The cap gate itself uses effective article states, so stale ledger values
    # never block approvals; this remains useful for reporting/history.
    ledger = await get_or_create_current_ledger(db, user_id)
    await db.execute(
        """UPDATE usage_ledger SET articles_started = articles_started + ?, updated_at = ?
           WHERE id = ?""",
        (articles_created, now, ledger["id"]),
    )

    # Transition batch to processed
    await db.execute(
        "UPDATE seed_batches SET status = 'processed' WHERE id = ?",
        (batch_id,),
    )

    # Log pipeline event
    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, batch_id, user_id, event_type, from_state, to_state, payload, created_at)
           VALUES (?, ?, ?, 'state_transition', 'waiting_approval', 'processed', ?, ?)""",
        (event_id, batch_id, user_id, json.dumps({"articles_created": articles_created}), now),
    )

    await db.commit()

    return {
        "articles_created": articles_created,
        "budget_limited": False,
    }
