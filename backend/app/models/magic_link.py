"""Magic link creation and verification."""

import hashlib
import secrets
from datetime import datetime, timezone, timedelta

import aiosqlite

from app.utils.ulid import generate_id
from app.utils.time import utc_now, is_expired


# Expiry durations by purpose
EXPIRY_MAP = {
    "login": timedelta(minutes=15),
    "admin": timedelta(minutes=15),
    "checkpoint_1": timedelta(days=7),
    "checkpoint_2": None,  # No expiry
}


async def create_magic_link(
    db: aiosqlite.Connection,
    user_id: str,
    purpose: str,
    reference_id: str | None = None,
    *,
    commit: bool = True,
) -> str:
    """Create a magic link. Returns the raw token (never stored)."""
    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    expiry_delta = EXPIRY_MAP.get(purpose)
    expires_at = None
    if expiry_delta is not None:
        expires_at = (datetime.now(timezone.utc) + expiry_delta).isoformat().replace("+00:00", "Z")

    link_id = generate_id()
    now = utc_now()

    await db.execute(
        """INSERT INTO magic_links (id, user_id, token_hash, purpose, reference_id, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (link_id, user_id, token_hash, purpose, reference_id, expires_at, now),
    )
    if commit:
        await db.commit()

    return raw_token


async def verify_magic_link(
    db: aiosqlite.Connection,
    raw_token: str,
    expected_purpose: str | None = None,
) -> dict | None:
    """Verify a magic link token. Returns link dict or None if invalid.
    
    Login links are single-use (consumed_at set on first verify).
    Checkpoint links are reusable.
    """
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    cursor = await db.execute(
        "SELECT * FROM magic_links WHERE token_hash = ?",
        (token_hash,),
    )
    row = await cursor.fetchone()

    if row is None:
        return None

    link = dict(row)

    # Check purpose match
    if expected_purpose and link["purpose"] != expected_purpose:
        return None

    # Check expiry
    if is_expired(link["expires_at"]):
        return None

    # Login and admin links are single-use
    if link["purpose"] in ("login", "admin"):
        if link["consumed_at"] is not None:
            return None
        # Consume it
        now = utc_now()
        await db.execute(
            "UPDATE magic_links SET consumed_at = ? WHERE id = ?",
            (now, link["id"]),
        )
        await db.commit()
        link["consumed_at"] = now

    return link
