"""Vault routes: image upload, list, update, delete, gallery."""

import json
import os
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session
from app.utils.ulid import generate_id
from app.utils.time import utc_now

router = APIRouter(prefix="/api/vault", tags=["vault"])

# Human-readable labels for article states
STATE_LABELS = {
    "OUTLINING": "Outlining",
    "DRAFTING": "Writing",
    "HUMANIZING": "Humanizing",
    "EDIT_REVIEW": "Reviewing",
    "MEDIA_ASSEMBLY": "Adding Images",
    "WAITING_CHECKPOINT_2": "Awaiting Review",
    "REVISION": "Revising",
    "READY_TO_PUBLISH": "Ready",
    "PUBLISHING": "Publishing",
}

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB

MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@router.get("/gallery")
async def gallery(request: Request):
    """Return all images organized by status for the gallery view."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        user_id = session["user_id"]

        # 1. Published images: article_images where article state is PUBLISHED
        cursor = await db.execute(
            """
            SELECT ai.id, ai.article_id, ai.storage_url, ai.alt_text,
                   ai.source_type, ai.section_heading, ai.created_at,
                   ai.width, ai.height,
                   i.title AS article_title, a.published_at
            FROM article_images ai
            JOIN articles a ON a.id = ai.article_id
            JOIN ideas i ON i.id = a.idea_id
            WHERE a.user_id = ? AND a.state = 'PUBLISHED'
            ORDER BY a.published_at DESC, ai.anchor_index
            """,
            (user_id,),
        )
        published_rows = [dict(r) for r in await cursor.fetchall()]

        # Group by article
        published_map: dict = {}
        for row in published_rows:
            aid = row["article_id"]
            if aid not in published_map:
                published_map[aid] = {
                    "article_id": aid,
                    "article_title": row["article_title"],
                    "published_at": row["published_at"],
                    "images": [],
                }
            published_map[aid]["images"].append({
                "id": row["id"],
                "storage_url": row["storage_url"],
                "alt_text": row["alt_text"],
                "source_type": row["source_type"],
                "section_heading": row["section_heading"],
                "created_at": row["created_at"],
                "width": row["width"],
                "height": row["height"],
            })
        published = list(published_map.values())

        # 2. In-progress images: article_images where article is NOT PUBLISHED/ARCHIVED
        cursor = await db.execute(
            """
            SELECT ai.id, ai.article_id, ai.storage_url, ai.alt_text,
                   ai.source_type, ai.section_heading, ai.created_at,
                   ai.width, ai.height,
                   i.title AS article_title, a.state AS article_state
            FROM article_images ai
            JOIN articles a ON a.id = ai.article_id
            JOIN ideas i ON i.id = a.idea_id
            WHERE a.user_id = ? AND a.state NOT IN ('PUBLISHED', 'ARCHIVED')
            ORDER BY a.updated_at DESC, ai.anchor_index
            """,
            (user_id,),
        )
        in_progress_rows = [dict(r) for r in await cursor.fetchall()]

        in_progress_map: dict = {}
        for row in in_progress_rows:
            aid = row["article_id"]
            if aid not in in_progress_map:
                in_progress_map[aid] = {
                    "article_id": aid,
                    "article_title": row["article_title"],
                    "article_state": row["article_state"],
                    "article_state_label": STATE_LABELS.get(row["article_state"], row["article_state"]),
                    "images": [],
                }
            in_progress_map[aid]["images"].append({
                "id": row["id"],
                "storage_url": row["storage_url"],
                "alt_text": row["alt_text"],
                "source_type": row["source_type"],
                "section_heading": row["section_heading"],
                "created_at": row["created_at"],
            })
        in_progress = list(in_progress_map.values())

        # 3. Seed images not yet used in articles
        # A seed image is "used" if an article_image exists with the same storage_path
        cursor = await db.execute(
            """
            SELECT si.id, si.seed_id, si.filename, si.storage_path, si.created_at,
                   s.content AS seed_content, s.seed_type,
                   sb.created_at AS batch_created_at
            FROM seed_images si
            JOIN seeds s ON s.id = si.seed_id
            JOIN seed_batches sb ON sb.id = s.batch_id
            WHERE sb.user_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM article_images ai
                  WHERE ai.storage_url = si.storage_path
              )
            ORDER BY si.created_at DESC
            """,
            (user_id,),
        )
        seed_img_rows = [dict(r) for r in await cursor.fetchall()]

        seed_groups: dict = {}
        for row in seed_img_rows:
            sid = row["seed_id"]
            if sid not in seed_groups:
                seed_groups[sid] = {
                    "group_type": "seed",
                    "seed_id": sid,
                    "seed_content": row["seed_content"],
                    "seed_type": row["seed_type"],
                    "batch_created_at": row["batch_created_at"],
                    "images": [],
                }
            seed_groups[sid]["images"].append({
                "id": row["id"],
                "storage_url": f"/api/vault/seed-image/{row['id']}",
                "filename": row["filename"],
                "created_at": row["created_at"],
            })

        # 4. Vault images (general pool)
        cursor = await db.execute(
            "SELECT * FROM vault_images WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        vault_rows = [dict(r) for r in await cursor.fetchall()]

        vault_group = {
            "group_type": "vault",
            "images": [
                {
                    "id": r["id"],
                    "storage_url": r["storage_url"],
                    "filename": r["filename"],
                    "description": r["description"],
                    "tags": r["tags"],
                    "used_count": r["used_count"],
                    "created_at": r["created_at"],
                }
                for r in vault_rows
            ],
        }

        available = list(seed_groups.values())
        if vault_group["images"]:
            available.append(vault_group)

    return {
        "published": published,
        "in_progress": in_progress,
        "available": available,
    }


@router.get("/seed-image/{seed_image_id}")
async def serve_seed_image(seed_image_id: str, request: Request):
    """Serve a seed image file. Requires authentication and ownership."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Look up seed image and verify ownership
        cursor = await db.execute(
            """SELECT si.storage_path FROM seed_images si
               JOIN seeds s ON s.id = si.seed_id
               JOIN seed_batches sb ON sb.id = s.batch_id
               WHERE si.id = ? AND sb.user_id = ?""",
            (seed_image_id, session["user_id"]),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Seed image not found")

    file_path = Path(row["storage_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")

    ext = file_path.suffix.lower()
    media_type = MIME_MAP.get(ext, "application/octet-stream")
    return FileResponse(str(file_path), media_type=media_type)


@router.get("/images/file/{user_id}/{filename}")
async def serve_vault_image(user_id: str, filename: str, request: Request):
    """Serve a vault image file. Requires authentication and ownership."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Verify ownership (user can only access their own images)
        if session["user_id"] != user_id:
            # Allow admins to access any user's images
            admin_emails = [e.strip() for e in (config.ADMIN_EMAILS or "").split(",") if e.strip()]
            cursor = await db.execute("SELECT email FROM users WHERE id = ?", (session["user_id"],))
            user = await cursor.fetchone()
            if not user or user["email"] not in admin_emails:
                raise HTTPException(status_code=403, detail="Access denied")

    # Serve file from local storage
    file_path = Path("data") / "vault" / user_id / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    ext = file_path.suffix.lower()
    media_type = MIME_MAP.get(ext, "application/octet-stream")
    return FileResponse(str(file_path), media_type=media_type)


@router.get("/images")
async def list_images(request: Request):
    """List all vault images for the current user."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            "SELECT * FROM vault_images WHERE user_id = ? ORDER BY created_at DESC",
            (session["user_id"],),
        )
        images = [dict(r) for r in await cursor.fetchall()]

    return {"images": images}


@router.post("/images")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """Upload an image to the vault."""
    config = request.app.state.config

    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Invalid file type. Use PNG, JPG, WebP, or GIF.")

    contents = await file.read()
    if len(contents) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max 10MB.")

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        image_id = generate_id()
        now = utc_now()

        # Store locally for dev
        storage_dir = os.path.join("data", "vault", session["user_id"])
        os.makedirs(storage_dir, exist_ok=True)
        ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "png"
        storage_path = os.path.join(storage_dir, f"{image_id}.{ext}")

        with open(storage_path, "wb") as f:
            f.write(contents)

        storage_url = f"/api/vault/images/file/{session['user_id']}/{image_id}.{ext}"

        await db.execute(
            """INSERT INTO vault_images (id, user_id, filename, storage_url, mime_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (image_id, session["user_id"], file.filename, storage_url, file.content_type, now),
        )
        await db.commit()

    return {"id": image_id, "storage_url": storage_url}


class UpdateImageRequest(BaseModel):
    description: str | None = None
    tags: str | None = None


@router.put("/images/{image_id}")
async def update_image(image_id: str, body: UpdateImageRequest, request: Request):
    """Update image description and tags."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            "SELECT * FROM vault_images WHERE id = ? AND user_id = ?",
            (image_id, session["user_id"]),
        )
        if not await cursor.fetchone():
            raise HTTPException(status_code=404, detail="Image not found")

        fields = []
        values = []
        if body.description is not None:
            fields.append("description = ?")
            values.append(body.description)
        if body.tags is not None:
            fields.append("tags = ?")
            values.append(body.tags)

        if not fields:
            raise HTTPException(status_code=422, detail="No fields to update")

        values.extend([image_id, session["user_id"]])
        await db.execute(
            f"UPDATE vault_images SET {', '.join(fields)} WHERE id = ? AND user_id = ?",
            values,
        )
        await db.commit()

    return {"status": "updated"}


@router.delete("/images/{image_id}")
async def delete_image(image_id: str, request: Request):
    """Delete a vault image."""
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        cursor = await db.execute(
            "SELECT * FROM vault_images WHERE id = ? AND user_id = ?",
            (image_id, session["user_id"]),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Image not found")

        await db.execute(
            "DELETE FROM vault_images WHERE id = ? AND user_id = ?",
            (image_id, session["user_id"]),
        )
        await db.commit()

    return {"status": "deleted"}
