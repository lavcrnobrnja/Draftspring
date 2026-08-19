#!/usr/bin/env python3
"""Database backup script. Copies SQLite DB to a timestamped backup.

Usage: python scripts/backup_db.py [--to-r2]

Without --to-r2: saves to data/backups/ locally.
With --to-r2: uploads to configured R2 bucket.

Designed to run every 6 hours via cron.
"""

import asyncio
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def backup_database(db_path: str, backup_dir: str) -> str:
    """Create a backup of the SQLite database using SQLite's backup API.
    
    Args:
        db_path: Path to the source database.
        backup_dir: Directory to store the backup.
    
    Returns:
        Path to the backup file.
    
    Raises:
        FileNotFoundError: If the source database doesn't exist.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")

    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"ghostwriter_backup_{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_name)

    # Use SQLite's built-in backup for consistency
    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(backup_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    return backup_path


def cleanup_old_backups(backup_dir: str, keep: int = 20) -> int:
    """Remove old backups, keeping the most recent `keep` files.
    
    Returns:
        Number of files removed.
    """
    if not os.path.exists(backup_dir):
        return 0

    backups = sorted(
        [f for f in os.listdir(backup_dir) if f.endswith(".db")],
        reverse=True,  # newest first
    )

    to_remove = backups[keep:]
    for old in to_remove:
        os.remove(os.path.join(backup_dir, old))

    return len(to_remove)


async def r2_upload(db_path: str, backup_path: str) -> bool:
    """Upload backup to R2/S3."""
    try:
        from app.config import Config
        config = Config()
        from app.storage.s3 import S3Storage
        storage = S3Storage(
            endpoint_url=config.S3_ENDPOINT_URL,
            access_key_id=config.S3_ACCESS_KEY_ID,
            secret_access_key=config.S3_SECRET_ACCESS_KEY,
            bucket_name=config.S3_BUCKET_NAME,
            public_url_prefix=config.S3_PUBLIC_URL_PREFIX,
        )
        key = f"backups/{os.path.basename(backup_path)}"
        with open(backup_path, "rb") as f:
            await storage.upload(key, f.read(), "application/x-sqlite3")
        return True
    except Exception as e:
        print(f"❌ R2 upload failed: {e}")
        return False


async def main():
    from app.config import Config
    config = Config()
    to_r2 = "--to-r2" in sys.argv

    db_path = os.path.abspath(config.DATABASE_PATH)
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")

    print(f"📦 Backing up {db_path}...")
    backup_path = backup_database(db_path, backup_dir)
    size_mb = os.path.getsize(backup_path) / (1024 * 1024)
    print(f"✅ Local backup: {backup_path} ({size_mb:.1f} MB)")

    removed = cleanup_old_backups(backup_dir, keep=20)
    if removed:
        print(f"🧹 Cleaned up {removed} old backup(s)")

    if to_r2:
        ok = await r2_upload(db_path, backup_path)
        if ok:
            print("✅ Uploaded to R2")
        else:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
