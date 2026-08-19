"""Task 1.4: Magic Links + Sessions tests."""

import hashlib
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from app.models.user import create_user
from app.models.magic_link import create_magic_link, verify_magic_link
from app.middleware.auth_middleware import create_session, get_current_session


@pytest.mark.asyncio
async def test_login_link_expiry(db):
    """Login magic link has 15 min expiry."""
    user = await create_user(db, "test@example.com")
    token = await create_magic_link(db, user["id"], "login")
    assert token is not None

    # Check expiry in DB
    cursor = await db.execute("SELECT expires_at FROM magic_links WHERE user_id = ?", (user["id"],))
    row = await cursor.fetchone()
    expires = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    diff = (expires - now).total_seconds()
    assert 14 * 60 < diff < 16 * 60  # ~15 minutes


@pytest.mark.asyncio
async def test_login_link_stored_as_hash(db):
    """Token stored as SHA-256 hash, not raw."""
    user = await create_user(db, "test@example.com")
    raw_token = await create_magic_link(db, user["id"], "login")

    cursor = await db.execute("SELECT token_hash FROM magic_links WHERE user_id = ?", (user["id"],))
    row = await cursor.fetchone()
    stored_hash = row[0]

    # Raw token should NOT equal stored hash
    assert stored_hash != raw_token
    # But hashing the raw token should match
    expected_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    assert stored_hash == expected_hash


@pytest.mark.asyncio
async def test_cp1_link_7_day_expiry(db):
    """CP1 magic link has 7 day expiry and reference_id."""
    user = await create_user(db, "test@example.com")
    token = await create_magic_link(db, user["id"], "checkpoint_1", reference_id="batch123")

    cursor = await db.execute(
        "SELECT expires_at, reference_id FROM magic_links WHERE user_id = ?",
        (user["id"],),
    )
    row = await cursor.fetchone()
    expires = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    diff = (expires - now).total_seconds()
    assert 6 * 86400 < diff < 8 * 86400  # ~7 days
    assert row[1] == "batch123"


@pytest.mark.asyncio
async def test_cp2_link_no_expiry(db):
    """CP2 magic link has no expiry."""
    user = await create_user(db, "test@example.com")
    await create_magic_link(db, user["id"], "checkpoint_2", reference_id="article123")

    cursor = await db.execute(
        "SELECT expires_at FROM magic_links WHERE user_id = ?",
        (user["id"],),
    )
    row = await cursor.fetchone()
    assert row[0] is None


@pytest.mark.asyncio
async def test_verify_valid_login_token(db):
    """Verify valid login token returns dict and sets consumed_at."""
    user = await create_user(db, "test@example.com")
    token = await create_magic_link(db, user["id"], "login")

    result = await verify_magic_link(db, token, expected_purpose="login")
    assert result is not None
    assert result["user_id"] == user["id"]
    assert result["purpose"] == "login"

    # Should be consumed
    cursor = await db.execute(
        "SELECT consumed_at FROM magic_links WHERE user_id = ?",
        (user["id"],),
    )
    row = await cursor.fetchone()
    assert row[0] is not None


@pytest.mark.asyncio
async def test_login_token_single_use(db):
    """Login token is single-use (second verify returns None)."""
    user = await create_user(db, "test@example.com")
    token = await create_magic_link(db, user["id"], "login")

    result1 = await verify_magic_link(db, token, expected_purpose="login")
    assert result1 is not None

    result2 = await verify_magic_link(db, token, expected_purpose="login")
    assert result2 is None


@pytest.mark.asyncio
async def test_cp1_token_reusable(db):
    """CP1 token is reusable (verify twice, both succeed)."""
    user = await create_user(db, "test@example.com")
    token = await create_magic_link(db, user["id"], "checkpoint_1", reference_id="batch1")

    result1 = await verify_magic_link(db, token, expected_purpose="checkpoint_1")
    assert result1 is not None

    result2 = await verify_magic_link(db, token, expected_purpose="checkpoint_1")
    assert result2 is not None


@pytest.mark.asyncio
async def test_expired_token_returns_none(db):
    """Expired token returns None."""
    user = await create_user(db, "test@example.com")
    token = await create_magic_link(db, user["id"], "login")

    # Manually expire it
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    await db.execute(
        "UPDATE magic_links SET expires_at = ? WHERE token_hash = ?",
        (past, token_hash),
    )
    await db.commit()

    result = await verify_magic_link(db, token, expected_purpose="login")
    assert result is None


@pytest.mark.asyncio
async def test_wrong_purpose_returns_none(db):
    """Wrong purpose returns None."""
    user = await create_user(db, "test@example.com")
    token = await create_magic_link(db, user["id"], "login")

    result = await verify_magic_link(db, token, expected_purpose="checkpoint_1")
    assert result is None


@pytest.mark.asyncio
async def test_tampered_token_returns_none(db):
    """Tampered token returns None."""
    user = await create_user(db, "test@example.com")
    await create_magic_link(db, user["id"], "login")

    result = await verify_magic_link(db, "tampered-token-value", expected_purpose="login")
    assert result is None


@pytest.mark.asyncio
async def test_create_session_full_scope(db):
    """Full session has 7-day expiry."""
    user = await create_user(db, "test@example.com")
    session_id = await create_session(db, user["id"], "full")

    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    session = dict(row)
    assert session["scope"] == "full"
    expires = datetime.fromisoformat(session["expires_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    diff = (expires - now).total_seconds()
    assert 6 * 86400 < diff < 8 * 86400  # ~7 days


@pytest.mark.asyncio
async def test_create_session_scoped(db):
    """Scoped session has 4-hour expiry."""
    user = await create_user(db, "test@example.com")
    session_id = await create_session(db, user["id"], "checkpoint_1", scope_ref="batch1")

    cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
    row = await cursor.fetchone()
    session = dict(row)
    assert session["scope"] == "checkpoint_1"
    assert session["scope_ref"] == "batch1"
    expires = datetime.fromisoformat(session["expires_at"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    diff = (expires - now).total_seconds()
    assert 3 * 3600 < diff < 5 * 3600  # ~4 hours


@pytest.mark.asyncio
async def test_get_current_session(db):
    """Get current session from request cookie."""
    user = await create_user(db, "test@example.com")
    session_id = await create_session(db, user["id"], "full")

    # Mock request with cookie
    request = MagicMock()
    request.cookies = {"session_id": session_id}

    session = await get_current_session(db, request)
    assert session is not None
    assert session["user_id"] == user["id"]


@pytest.mark.asyncio
async def test_get_current_session_expired(db):
    """Expired session returns None."""
    user = await create_user(db, "test@example.com")
    session_id = await create_session(db, user["id"], "full")

    # Manually expire
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await db.execute("UPDATE sessions SET expires_at = ? WHERE id = ?", (past, session_id))
    await db.commit()

    request = MagicMock()
    request.cookies = {"session_id": session_id}

    session = await get_current_session(db, request)
    assert session is None
