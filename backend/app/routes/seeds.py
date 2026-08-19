"""Seed submission route."""

import os
import re
from typing import Literal

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, field_validator

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session
from app.middleware.subscription import require_active_subscription
from app.models.seed_batch import create_seed_batch
from app.utils.ulid import generate_id
from app.utils.time import utc_now
from app.image_styles import validate_image_style_pair

router = APIRouter(prefix="/api", tags=["seeds"])

URL_PATTERN = re.compile(r"^https?://\S+$")


class SeedInput(BaseModel):
    seed_type: Literal["topic", "url"] | None = None
    type: Literal["topic", "url"] | None = None
    content: str

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Content cannot be empty")
        return v.strip()

    def get_seed_type(self) -> str:
        """Accept both 'seed_type' and 'type' fields."""
        return self.seed_type or self.type or "topic"


class SeedSubmission(BaseModel):
    seeds: list[SeedInput]

    @field_validator("seeds")
    @classmethod
    def validate_seeds(cls, v):
        if len(v) == 0:
            raise ValueError("At least one seed is required")
        if len(v) > 10:
            raise ValueError("Maximum 10 seeds per batch")
        # Validate each seed has at least one type field
        for seed in v:
            st = seed.seed_type or seed.type or "topic"
            if st == "url" and not URL_PATTERN.match(seed.content):
                raise ValueError(f"Invalid URL: {seed.content}")
        return v


class ContentBriefSubmission(BaseModel):
    """New content brief format: single description + optional reference URLs + keywords."""
    description: str
    reference_urls: list[str] | None = None
    keywords: str | None = None
    image_style: str | None = None
    image_substyle: str | None = None

    @field_validator("description")
    @classmethod
    def description_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Description is required")
        return v.strip()

    @field_validator("reference_urls")
    @classmethod
    def validate_urls(cls, v):
        if v is None:
            return v
        if len(v) > 3:
            raise ValueError("Maximum 3 reference URLs")
        for url in v:
            if url.strip() and not URL_PATTERN.match(url.strip()):
                raise ValueError(f"Invalid URL: {url}")
        return [u.strip() for u in v if u.strip()]


@router.post("/seeds", status_code=201)
async def submit_seeds(request: Request):
    """Submit seed topics/URLs for article ideation.
    
    Accepts either:
    - New format: {description, reference_urls, keywords} (content brief)
    - Legacy format: {seeds: [{seed_type, content}]}
    """
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        # Auth check FIRST — before body parsing
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # Now parse body
        try:
            raw = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid request body")

        user_id = session["user_id"]

        # Get user details
        cursor = await db.execute(
            "SELECT subscription_status, ghost_key_valid FROM users WHERE id = ?",
            (user_id,),
        )
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Subscription check
        require_active_subscription(dict(user))

        # Ghost connection check — env fallback counts as valid
        from app.config import get_config
        _cfg = get_config()
        if not user["ghost_key_valid"] and not (_cfg.GHOST_URL and _cfg.GHOST_ADMIN_API_KEY):
            raise HTTPException(status_code=400, detail="Ghost connection required. Connect your Ghost blog in Settings first.")

        # Detect format: new (content brief) vs legacy (seeds array)
        if "description" in raw:
            # New content brief format
            try:
                body = ContentBriefSubmission(**raw)
            except Exception:
                raise HTTPException(status_code=422, detail="Invalid request body")

            seeds_data = []

            image_style = None
            image_substyle = None
            if body.image_style is not None or body.image_substyle is not None:
                try:
                    image_style, image_substyle = validate_image_style_pair(body.image_style, body.image_substyle)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

            # Primary seed: topic from description + keywords
            content = body.description
            if body.keywords:
                content = f"{body.description}\n\nKeywords: {body.keywords}"
            seeds_data.append({"seed_type": "topic", "content": content})

            # Additional seeds for reference URLs
            for url in (body.reference_urls or []):
                seeds_data.append({"seed_type": "url", "content": url})

            batch_id, seed_ids = await create_seed_batch(
                db, user_id, seeds_data,
                image_style=image_style,
                image_substyle=image_substyle,
            )

            return {"batch_id": batch_id, "seed_count": len(seeds_data), "seed_ids": seed_ids}

        else:
            # Legacy format
            try:
                body = SeedSubmission(**raw)
            except Exception:
                raise HTTPException(status_code=422, detail="Invalid request body")

            seeds_data = [{"seed_type": s.get_seed_type(), "content": s.content} for s in body.seeds]
            batch_id, seed_ids = await create_seed_batch(db, user_id, seeds_data)

            return {"batch_id": batch_id, "seed_count": len(body.seeds), "seed_ids": seed_ids}


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB


@router.post("/seeds/{seed_id}/images", status_code=201)
async def upload_seed_image(
    seed_id: str,
    request: Request,
    file: UploadFile = File(...),
    image_role: str = Form(default=None),
):
    """Upload an image for a specific seed.
    
    image_role: optional, 'cover' or 'body' for content brief photos.
    """
    config = request.app.state.config

    # Validate image_role if provided
    if image_role is not None and image_role not in ("cover", "body"):
        raise HTTPException(status_code=400, detail="image_role must be 'cover' or 'body'")

    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")

        user_id = session["user_id"]

        # Subscription check
        cursor = await db.execute(
            "SELECT subscription_status FROM users WHERE id = ?", (user_id,)
        )
        u = await cursor.fetchone()
        if u:
            require_active_subscription(dict(u))

        # Verify seed exists and belongs to user
        cursor = await db.execute(
            """SELECT s.id, s.batch_id FROM seeds s
               JOIN seed_batches sb ON s.batch_id = sb.id
               WHERE s.id = ? AND sb.user_id = ?""",
            (seed_id, user_id),
        )
        seed_row = await cursor.fetchone()
        if not seed_row:
            raise HTTPException(status_code=404, detail="Seed not found")

        # Check existing image count (max 2 per seed)
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM seed_images WHERE seed_id = ?", (seed_id,)
        )
        count_row = await cursor.fetchone()
        if count_row["cnt"] >= 2:
            raise HTTPException(status_code=400, detail="Maximum 2 images per seed")

        # Validate file type
        if file.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid image type: {file.content_type}")

        # Read and check size
        content = await file.read()
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(status_code=400, detail="Image too large (max 5MB)")

        # Store file
        batch_id = seed_row["batch_id"]
        image_id = generate_id()
        storage_dir = os.path.join("data", "seed_images", batch_id)
        os.makedirs(storage_dir, exist_ok=True)

        ext = os.path.splitext(file.filename or "image.png")[1] or ".png"
        storage_path = os.path.join(storage_dir, f"{image_id}{ext}")

        with open(storage_path, "wb") as f:
            f.write(content)

        now = utc_now()
        await db.execute(
            """INSERT INTO seed_images (id, seed_id, filename, storage_path, mime_type, image_role, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (image_id, seed_id, file.filename or f"{image_id}{ext}", storage_path, file.content_type, image_role, None, now),
        )
        await db.commit()

    return {
        "id": image_id,
        "seed_id": seed_id,
        "filename": file.filename,
        "mime_type": file.content_type,
        "image_role": image_role,
    }
