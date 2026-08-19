"""User CRUD operations."""

import aiosqlite

from app.utils.ulid import generate_id
from app.utils.time import utc_now
from app.services.encryption import encrypt


class DuplicateEmailError(Exception):
    """Raised when attempting to create a user with a duplicate email."""
    pass


def _row_to_dict(row: aiosqlite.Row) -> dict:
    """Convert a database row to a dictionary."""
    return dict(row)


async def create_user(db: aiosqlite.Connection, email: str) -> dict:
    """Create a new user with defaults. Returns user dict."""
    user_id = generate_id()
    now = utc_now()

    try:
        await db.execute(
            """INSERT INTO users (id, email, publish_days, created_at, updated_at)
               VALUES (?, ?, '[]', ?, ?)""",
            (user_id, email, now, now),
        )
        await db.commit()
    except aiosqlite.IntegrityError as e:
        if "UNIQUE" in str(e).upper() or "unique" in str(e).lower():
            raise DuplicateEmailError(f"Email {email} already exists") from e
        raise

    return await get_user_by_id(db, user_id)


async def get_user_by_id(db: aiosqlite.Connection, user_id: str) -> dict | None:
    """Get user by ID. Returns dict or None."""
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def get_user_by_email(db: aiosqlite.Connection, email: str) -> dict | None:
    """Get user by email. Returns dict or None."""
    cursor = await db.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = await cursor.fetchone()
    return _row_to_dict(row) if row else None


async def update_user(db: aiosqlite.Connection, user_id: str, **fields) -> dict:
    """Update user fields. Ghost API key is encrypted before storage."""
    # Handle ghost_admin_api_key specially — encrypt it
    if "ghost_admin_api_key" in fields:
        raw_key = fields.pop("ghost_admin_api_key")
        fields["ghost_admin_api_key_enc"] = encrypt(raw_key)

    fields["updated_at"] = utc_now()

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [user_id]

    await db.execute(
        f"UPDATE users SET {set_clause} WHERE id = ?",
        values,
    )
    await db.commit()

    return await get_user_by_id(db, user_id)
