"""Database connection factory and migration runner."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


@asynccontextmanager
async def get_connection(db_path: str):
    """Create an async SQLite connection with FK enforcement and WAL mode."""
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

    db = await aiosqlite.connect(db_path, timeout=30.0)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db
    finally:
        await db.close()


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Run pending SQL migrations tracked in _migrations table."""
    # Create migrations tracking table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    await db.commit()

    # Get already applied migrations
    cursor = await db.execute("SELECT filename FROM _migrations")
    applied = {row[0] for row in await cursor.fetchall()}

    # Find and run pending migrations
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for migration_file in migration_files:
        if migration_file.name not in applied:
            sql = migration_file.read_text()
            # Split on semicolons and execute each statement
            # (PRAGMA statements need individual execution)
            statements = [s.strip() for s in sql.split(";") if s.strip()]
            for statement in statements:
                await db.execute(statement)
            await db.execute(
                "INSERT INTO _migrations (filename) VALUES (?)",
                (migration_file.name,),
            )
            await db.commit()
