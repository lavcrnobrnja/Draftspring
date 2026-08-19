"""Session management and auth middleware."""

from datetime import datetime, timezone, timedelta
from typing import Callable

import aiosqlite
from fastapi import Request, HTTPException

from app.utils.ulid import generate_id
from app.utils.time import utc_now, is_expired


# Session expiry by scope
SESSION_EXPIRY = {
    "full": timedelta(days=7),
    "admin": timedelta(days=7),
    "checkpoint_1": timedelta(hours=4),
    "checkpoint_2": timedelta(hours=4),
}


async def create_session(
    db: aiosqlite.Connection,
    user_id: str,
    scope: str,
    scope_ref: str | None = None,
) -> str:
    """Create a new session. Returns session_id."""
    session_id = generate_id()
    now = utc_now()
    expiry_delta = SESSION_EXPIRY.get(scope, timedelta(hours=4))
    expires_at = (datetime.now(timezone.utc) + expiry_delta).isoformat().replace("+00:00", "Z")

    await db.execute(
        """INSERT INTO sessions (id, user_id, scope, scope_ref, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, user_id, scope, scope_ref, expires_at, now),
    )
    await db.commit()

    return session_id


async def get_current_session(
    db: aiosqlite.Connection,
    request: Request,
) -> dict | None:
    """Get the current session from request cookie. Returns None if expired or missing."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None

    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    if row is None:
        return None

    session = dict(row)

    # Check expiry
    if is_expired(session["expires_at"]):
        return None

    return session


async def delete_session(db: aiosqlite.Connection, session_id: str) -> None:
    """Delete a session."""
    await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    await db.commit()


def require_auth(scope: str | None = None) -> Callable:
    """FastAPI dependency factory for auth. Validates session and optional scope."""

    async def dependency(request: Request, db: aiosqlite.Connection = None):
        session_id = request.cookies.get("session_id")
        if not session_id:
            raise HTTPException(status_code=401, detail="Not authenticated")

        # db needs to be injected via FastAPI's Depends - this is a factory
        # The actual db connection will come from the route's dependency
        raise HTTPException(status_code=401, detail="Not authenticated")

    return dependency
