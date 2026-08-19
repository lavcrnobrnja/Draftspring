"""Task 1.3: User CRUD tests."""

import pytest

from app.models.user import (
    create_user,
    get_user_by_id,
    get_user_by_email,
    update_user,
    DuplicateEmailError,
)
from app.services.encryption import decrypt


@pytest.mark.asyncio
async def test_create_user(db):
    """Create user with valid email, assert defaults."""
    user = await create_user(db, "alice@example.com")
    assert user["email"] == "alice@example.com"
    assert user["id"] is not None
    assert len(user["id"]) == 26  # ULID
    assert user["subscription_status"] == "none"
    assert user["default_word_count"] == 1500
    assert user["ghost_key_valid"] == 0
    assert user["created_at"] is not None
    assert user["updated_at"] is not None


@pytest.mark.asyncio
async def test_duplicate_email_error(db):
    """Duplicate email raises DuplicateEmailError."""
    await create_user(db, "bob@example.com")
    with pytest.raises(DuplicateEmailError):
        await create_user(db, "bob@example.com")


@pytest.mark.asyncio
async def test_get_user_by_id(db):
    """Get user by ID returns correct user."""
    user = await create_user(db, "carol@example.com")
    found = await get_user_by_id(db, user["id"])
    assert found is not None
    assert found["email"] == "carol@example.com"


@pytest.mark.asyncio
async def test_get_user_by_id_nonexistent(db):
    """Nonexistent user returns None."""
    found = await get_user_by_id(db, "nonexistent")
    assert found is None


@pytest.mark.asyncio
async def test_get_user_by_email(db):
    """Get user by email returns correct user."""
    user = await create_user(db, "dave@example.com")
    found = await get_user_by_email(db, "dave@example.com")
    assert found is not None
    assert found["id"] == user["id"]


@pytest.mark.asyncio
async def test_get_user_by_email_nonexistent(db):
    """Nonexistent email returns None."""
    found = await get_user_by_email(db, "nobody@example.com")
    assert found is None


@pytest.mark.asyncio
async def test_update_user_single_field(db):
    """Update single field, assert updated_at changes."""
    user = await create_user(db, "eve@example.com")
    original_updated = user["updated_at"]

    import time
    time.sleep(0.01)

    updated = await update_user(db, user["id"], brand_voice="Casual and fun")
    assert updated["brand_voice"] == "Casual and fun"
    assert updated["updated_at"] != original_updated


@pytest.mark.asyncio
async def test_update_ghost_key_encrypted(db):
    """Update Ghost API key → raw DB value is encrypted, decrypt matches original."""
    user = await create_user(db, "frank@example.com")
    raw_key = "abc123:deadbeef1234567890"

    await update_user(db, user["id"], ghost_admin_api_key=raw_key)

    # Read raw DB value
    cursor = await db.execute(
        "SELECT ghost_admin_api_key_enc FROM users WHERE id = ?", (user["id"],)
    )
    row = await cursor.fetchone()
    encrypted_value = row[0]

    # Encrypted value is NOT the raw key
    assert encrypted_value != raw_key
    assert encrypted_value is not None

    # But decrypting it gives back the original
    assert decrypt(encrypted_value) == raw_key
