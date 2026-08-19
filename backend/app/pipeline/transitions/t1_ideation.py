"""T1: Seed Batch → Ideation. Load seeds → LLM → insert ideas → transition."""

import json

import aiosqlite

from app.llm.base import LLMProvider
from app.models.seed_batch import get_batch_with_seeds
from app.utils.ulid import generate_id
from app.utils.time import utc_now
from app.utils.url_fetcher import fetch_url_content


async def run_ideation(
    db: aiosqlite.Connection,
    config,
    batch_id: str,
    llm: LLMProvider,
    ideas_per_seed: int = 3,
    ideas_requested: int | None = None,
) -> dict:
    """Run ideation for a seed batch.
    
    1. Load seeds from batch
    2. Call LLM to generate ideas
    3. Insert ideas into DB
    4. Create CP1 magic link
    5. Send email
    6. Transition batch to waiting_approval
    """
    batch = await get_batch_with_seeds(db, batch_id)
    if not batch:
        return {"success": False, "error": "Batch not found"}

    user_id = batch["user_id"]
    regen_feedback = batch.get("regen_feedback")

    # Get user email, ghost_url, and brand_voice
    cursor = await db.execute("SELECT email, ghost_url, brand_voice FROM users WHERE id = ?", (user_id,))
    user_row = await cursor.fetchone()
    if not user_row:
        return {"success": False, "error": "User not found"}

    user_ghost_url = user_row["ghost_url"] or getattr(config, "GHOST_URL", None) or "blog"
    user_brand_voice = user_row["brand_voice"]

    # Get existing titles to avoid duplication
    cursor = await db.execute(
        "SELECT title FROM ideas WHERE batch_id IN (SELECT id FROM seed_batches WHERE user_id = ?)",
        (user_id,),
    )
    existing_titles = [row["title"] for row in await cursor.fetchall()]

    # Get rejected titles from THIS batch for regen (so LLM avoids them)
    cursor = await db.execute(
        "SELECT title FROM ideas WHERE batch_id = ? AND status = 'rejected'",
        (batch_id,),
    )
    rejected_titles = [row["title"] for row in await cursor.fetchall()]

    # Fetch URL content for URL seeds and load seed images
    seeds_data = []
    all_seed_images = []
    for s in batch["seeds"]:
        seed_entry = {"seed_type": s["seed_type"], "content": s["content"]}
        if s["seed_type"] == "url":
            fetched = await fetch_url_content(s["content"])
            if fetched["extracted_content"]:
                seed_entry["extracted_content"] = fetched["extracted_content"]
                # Store extracted content on seed record for downstream brief assembly
                await db.execute(
                    "UPDATE seeds SET extracted_content = ? WHERE id = ?",
                    (fetched["extracted_content"][:3000], s["id"]),
                )
            else:
                seed_entry["extracted_content"] = f"[Could not fetch content: {fetched['error']}]"
                await db.execute(
                    "UPDATE seeds SET extracted_content = ? WHERE id = ?",
                    (f"[Could not fetch content: {fetched['error']}]", s["id"]),
                )

        # Check for attached seed images
        cursor = await db.execute(
            "SELECT id, filename, storage_path, image_role, description FROM seed_images WHERE seed_id = ?", (s["id"],)
        )
        seed_imgs = [dict(r) for r in await cursor.fetchall()]
        if seed_imgs:
            seed_entry["attached_images"] = len(seed_imgs)
            all_seed_images.extend(seed_imgs)

        seeds_data.append(seed_entry)

    # Photo description pre-processing: describe images that lack descriptions
    for img in all_seed_images:
        if not img.get("description") and img.get("storage_path"):
            import os
            if os.path.exists(img["storage_path"]):
                try:
                    with open(img["storage_path"], "rb") as f:
                        image_bytes = f.read()
                    description = await llm.describe_image(image_bytes)
                    await db.execute(
                        "UPDATE seed_images SET description = ? WHERE id = ?",
                        (description, img["id"]),
                    )
                    img["description"] = description
                except Exception:
                    pass  # Store image without description, downstream handles null

    # Build user_images list for brief format
    user_images = []
    for img in all_seed_images:
        entry = {"role": img.get("image_role") or "body", "filename": img.get("filename", "")}
        if img.get("description"):
            entry["description"] = img["description"]
        user_images.append(entry)

    # Determine effective ideas count
    num_ideas = ideas_requested or ideas_per_seed

    # Call LLM (pass feedback + rejected titles for regen)
    result = await llm.generate_ideas(
        seeds_data, num_ideas, existing_titles,
        feedback=regen_feedback, rejected_titles=rejected_titles or None,
        ghost_url=user_ghost_url, brand_voice=user_brand_voice,
        user_images=user_images if user_images else None,
    )

    # Insert ideas
    now = utc_now()
    for idea in result["ideas"]:
        # seed_index is optional in new brief format; default to first seed
        seed_index = idea.get("seed_index", 0)
        seed_id = batch["seeds"][seed_index]["id"] if seed_index < len(batch["seeds"]) else batch["seeds"][0]["id"]
        idea_id = generate_id()
        await db.execute(
            """INSERT INTO ideas (id, batch_id, seed_id, title, angle, target_keyword,
               search_intent, estimated_volume, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                idea_id, batch_id, seed_id,
                idea["title"], idea["angle"], idea["target_keyword"],
                idea.get("search_intent", ""),
                idea.get("estimated_search_volume", "low"),
                now,
            ),
        )

    # Transition batch
    await db.execute(
        "UPDATE seed_batches SET status = 'waiting_approval' WHERE id = ?",
        (batch_id,),
    )

    # Log pipeline event
    event_id = generate_id()
    await db.execute(
        """INSERT INTO pipeline_events (id, batch_id, user_id, event_type, from_state, to_state, payload, created_at)
           VALUES (?, ?, ?, 'state_transition', 'processing_ideation', 'waiting_approval', ?, ?)""",
        (event_id, batch_id, user_id, json.dumps({"ideas_count": len(result["ideas"])}), now),
    )

    await db.commit()
    return {"success": True, "ideas_count": len(result["ideas"])}
