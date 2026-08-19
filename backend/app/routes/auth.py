"""Auth routes: magic link login, verify, logout."""

import time
from collections import defaultdict

from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel, EmailStr

from app.database import get_connection
from app.models.user import create_user, get_user_by_email, DuplicateEmailError
from app.models.magic_link import create_magic_link, verify_magic_link
from app.middleware.auth_middleware import create_session, delete_session
from app.services.email import send_magic_link_email

router = APIRouter(prefix="/auth", tags=["auth"])

# Simple in-memory rate limiter
_rate_limit: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_WINDOW = 3600  # 1 hour
RATE_LIMIT_PER_EMAIL = 10  # per email per hour
RATE_LIMIT_GLOBAL = 100  # total across all emails per hour


class LoginRequest(BaseModel):
    email: EmailStr


def _is_rate_limited(key: str, max_requests: int) -> bool:
    """Returns True if rate limited. Does NOT record the attempt."""
    now = time.time()
    _rate_limit[key] = [t for t in _rate_limit[key] if now - t < RATE_LIMIT_WINDOW]
    return len(_rate_limit[key]) >= max_requests


def _record_attempt(key: str) -> None:
    """Record a rate limit attempt after checks pass."""
    _rate_limit[key].append(time.time())


@router.post("/request")
async def request_magic_link(body: LoginRequest, request: Request):
    """Request a magic link for login."""
    config = request.app.state.config

    if _is_rate_limited(body.email, RATE_LIMIT_PER_EMAIL) or _is_rate_limited("__global__", RATE_LIMIT_GLOBAL):
        raise HTTPException(status_code=429, detail="Too many requests")

    _record_attempt(body.email)
    _record_attempt("__global__")

    async with get_connection(config.DATABASE_PATH) as db:
        # Get or create user
        user = await get_user_by_email(db, body.email)
        if user is None:
            user = await create_user(db, body.email)
        elif user.get("email_bounce", 0):
            raise HTTPException(status_code=400, detail="Email address has bounced")

        # Create magic link
        token = await create_magic_link(db, user["id"], "login")

        # Send email
        sent = await send_magic_link_email(config, body.email, token, "login")
        if not sent:
            raise HTTPException(status_code=503, detail="Failed to send email. Please try again later.")

    result = {"message": "Magic link sent"}

    # In development mode, return the verify URL directly (no email service configured)
    if config.APP_ENV == "development":
        result["dev_verify_url"] = f"{config.APP_BASE_URL}/auth/verify?token={token}"

    return result


@router.get("/verify")
async def verify_token(token: str, request: Request, response: Response):
    """Verify a magic link token and create session."""
    config = request.app.state.config
    base_url = config.APP_BASE_URL

    async with get_connection(config.DATABASE_PATH) as db:
        # Try each purpose
        link = await verify_magic_link(db, token)

        if link is None:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(
                url="/login?error=invalid_token",
                status_code=307,
            )

        purpose = link["purpose"]

        # Create appropriate session — use relative paths so redirects work
        # regardless of what host/port the user is accessing from
        if purpose == "login":
            session_id = await create_session(db, link["user_id"], "full")
            redirect_url = "/dashboard"
        elif purpose == "admin":
            session_id = await create_session(db, link["user_id"], "admin")
            redirect_url = "/daddyo/"
        elif purpose == "checkpoint_1":
            session_id = await create_session(
                db, link["user_id"], "checkpoint_1",
                scope_ref=link.get("reference_id"),
            )
            redirect_url = f"/review/ideas/{link.get('reference_id', '')}"
        elif purpose == "checkpoint_2":
            session_id = await create_session(
                db, link["user_id"], "checkpoint_2",
                scope_ref=link.get("reference_id"),
            )
            redirect_url = f"/review/article/{link.get('reference_id', '')}"
            # Pass through action param (e.g. ?action=approve)
            action = request.query_params.get("action")
            if action in ("approve",):
                redirect_url += f"?action={action}"
        else:
            session_id = await create_session(db, link["user_id"], "full")
            redirect_url = "/dashboard"

        from fastapi.responses import RedirectResponse
        resp = RedirectResponse(url=redirect_url, status_code=307)
        resp.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            secure=config.COOKIE_SECURE,
            samesite=config.COOKIE_SAMESITE,
            domain=config.COOKIE_DOMAIN if config.COOKIE_DOMAIN != "localhost" else None,
        )
        return resp


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Logout: delete session and clear cookie."""
    config = request.app.state.config
    session_id = request.cookies.get("session_id")

    if session_id:
        async with get_connection(config.DATABASE_PATH) as db:
            await delete_session(db, session_id)

    response = Response(content='{"message": "Logged out"}', media_type="application/json")
    response.delete_cookie("session_id")
    return response
