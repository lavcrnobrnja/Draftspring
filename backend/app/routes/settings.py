"""Settings routes: Ghost connection, schedule, profile."""

import json
import zoneinfo

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from app.database import get_connection
from app.middleware.auth_middleware import get_current_session
from app.models.user import get_user_by_id, update_user
from app.services.ghost import validate_ghost_connection, fetch_ghost_staff
from app.image_styles import validate_image_style_pair

router = APIRouter(prefix="/api/settings", tags=["settings"])

VALID_DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


async def _get_authed_user(request: Request) -> dict:
    """Get the authenticated user or raise 401."""
    config = request.app.state.config
    async with get_connection(config.DATABASE_PATH) as db:
        session = await get_current_session(db, request)
        if not session:
            raise HTTPException(status_code=401, detail="Not authenticated")
        user = await get_user_by_id(db, session["user_id"])
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user


@router.get("")
async def get_settings(request: Request):
    """Get user settings. Never returns API key."""
    user = await _get_authed_user(request)
    # Remove sensitive fields
    safe_fields = {
        "id", "email", "ghost_url", "ghost_site_title", "ghost_version",
        "ghost_key_valid", "ghost_author_id", "ghost_author_name",
        "subscription_status", "publish_days",
        "publish_time", "publish_timezone", "articles_per_cycle_limit",
        "brand_voice", "default_word_count", "image_style", "image_substyle", "created_at",
    }
    return {k: user[k] for k in safe_fields if k in user}


class GhostSettings(BaseModel):
    ghost_url: str
    ghost_admin_api_key: str


def _normalize_ghost_url(url: str) -> str:
    """Normalize Ghost URL for uniqueness comparison: lowercase, strip trailing slash and protocol."""
    url = url.strip().lower()
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    return url.rstrip("/")


@router.put("/ghost")
async def update_ghost_settings(body: GhostSettings, request: Request):
    """Validate and save Ghost connection. Saves even if validation fails (marks as unvalidated)."""
    user = await _get_authed_user(request)
    config = request.app.state.config

    # Check uniqueness: one Ghost blog per account (exclude current user)
    normalized_url = _normalize_ghost_url(body.ghost_url)
    async with get_connection(config.DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT id, ghost_url FROM users WHERE id != ? AND ghost_key_valid = 1 AND ghost_url IS NOT NULL",
            (user["id"],),
        )
        rows = await cursor.fetchall()
        for row in rows:
            if _normalize_ghost_url(row["ghost_url"]) == normalized_url:
                raise HTTPException(
                    status_code=409,
                    detail="This blog has already automated content publishing in another account.",
                )

    # Try to validate, but save regardless
    result = await validate_ghost_connection(body.ghost_url, body.ghost_admin_api_key)
    valid = result.get("valid", False)

    async with get_connection(config.DATABASE_PATH) as db:
        # Clear previous author selection on reconnect (may be a different blog)
        updated = await update_user(
            db, user["id"],
            ghost_url=body.ghost_url,
            ghost_admin_api_key=body.ghost_admin_api_key,
            ghost_site_title=result.get("site_title", "") if valid else "",
            ghost_version=result.get("version", "") if valid else "",
            ghost_key_valid=1 if valid else 0,
            ghost_author_id=None,
            ghost_author_name=None,
        )

    if valid:
        # Fetch staff list so frontend can show author picker
        staff_result = await fetch_ghost_staff(body.ghost_url, body.ghost_admin_api_key)
        return {"message": "Ghost connection saved and verified", "site_title": result.get("site_title"), "valid": True, "staff": staff_result["staff"]}
    else:
        return {"message": "Ghost credentials saved but could not verify connection. Will retry when publishing.",
                "error": result.get("error", "Connection failed"), "valid": False}


class GhostAuthorSettings(BaseModel):
    ghost_author_id: str | None = None
    ghost_author_name: str | None = None


@router.put("/ghost/author")
async def update_ghost_author(body: GhostAuthorSettings, request: Request):
    """Save the selected Ghost author for publishing."""
    user = await _get_authed_user(request)
    config = request.app.state.config

    async with get_connection(config.DATABASE_PATH) as db:
        await update_user(
            db, user["id"],
            ghost_author_id=body.ghost_author_id,
            ghost_author_name=body.ghost_author_name,
        )

    return {"message": "Author updated", "ghost_author_id": body.ghost_author_id, "ghost_author_name": body.ghost_author_name}


@router.get("/ghost/staff")
async def get_ghost_staff(request: Request):
    """Fetch staff users from the connected Ghost blog."""
    user = await _get_authed_user(request)

    if not user.get("ghost_key_valid"):
        raise HTTPException(status_code=400, detail="Ghost not connected")

    from app.services.encryption import decrypt

    ghost_url = user.get("ghost_url")
    ghost_key_enc = user.get("ghost_admin_api_key_enc")
    if not ghost_url or not ghost_key_enc:
        raise HTTPException(status_code=400, detail="Ghost not connected")

    ghost_api_key = decrypt(ghost_key_enc)
    result = await fetch_ghost_staff(ghost_url, ghost_api_key)

    if result["error"]:
        # Key is bad — mark it invalid so the UI reflects the real state
        async with get_connection(request.app.state.config.DATABASE_PATH) as db:
            await update_user(db, user["id"], ghost_key_valid=0)

        return {"staff": [], "error": result["error"]}

    return {"staff": result["staff"]}


class ScheduleSettings(BaseModel):
    publish_days: str | list = None
    publish_time: str = None
    publish_timezone: str = None


@router.put("/schedule")
async def update_schedule(body: ScheduleSettings, request: Request):
    """Validate and save publish schedule."""
    user = await _get_authed_user(request)
    config = request.app.state.config

    # Accept both array and JSON string
    if isinstance(body.publish_days, list):
        days = body.publish_days
    else:
        try:
            days = json.loads(body.publish_days)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=422, detail="Invalid publish_days format")

    if not isinstance(days, list):
        raise HTTPException(status_code=422, detail="publish_days must be a list")
    if len(days) > 2:
        raise HTTPException(status_code=422, detail="Maximum 2 publish days allowed")
    if not all(d in VALID_DAYS for d in days):
        raise HTTPException(status_code=422, detail="Invalid day name")

    # Validate time
    try:
        parts = body.publish_time.split(":")
        h, m = int(parts[0]), int(parts[1])
        if h < 0 or h > 23 or m < 0 or m > 59:
            raise ValueError()
    except (ValueError, IndexError):
        raise HTTPException(status_code=422, detail="Invalid time format (expected HH:MM)")

    # Validate timezone
    try:
        zoneinfo.ZoneInfo(body.publish_timezone)
    except (KeyError, zoneinfo.ZoneInfoNotFoundError):
        raise HTTPException(status_code=422, detail="Invalid timezone")

    # Store as JSON string
    days_json = json.dumps(days)

    async with get_connection(config.DATABASE_PATH) as db:
        await update_user(
            db, user["id"],
            publish_days=days_json,
            publish_time=body.publish_time,
            publish_timezone=body.publish_timezone,
        )

    return {"message": "Schedule updated"}


class ProfileSettings(BaseModel):
    brand_voice: str | None = None
    default_word_count: int | None = None
    image_style: str | None = None
    image_substyle: str | None = None


@router.put("/profile")
async def update_profile(body: ProfileSettings, request: Request):
    """Update brand voice and word count."""
    user = await _get_authed_user(request)
    config = request.app.state.config

    fields = {}
    if body.brand_voice is not None:
        fields["brand_voice"] = body.brand_voice
    if body.default_word_count is not None:
        fields["default_word_count"] = body.default_word_count
    if body.image_style is not None or body.image_substyle is not None:
        try:
            image_style, image_substyle = validate_image_style_pair(body.image_style, body.image_substyle)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        fields["image_style"] = image_style
        fields["image_substyle"] = image_substyle

    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update")

    async with get_connection(config.DATABASE_PATH) as db:
        updated = await update_user(db, user["id"], **fields)

    safe_fields = {"brand_voice", "default_word_count", "image_style", "image_substyle"}
    return {k: updated[k] for k in safe_fields}
