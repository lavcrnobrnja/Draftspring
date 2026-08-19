"""Shared test fixtures."""

import os
import tempfile

import pytest
import pytest_asyncio

# Set test environment before importing app modules
os.environ["APP_ENV"] = "test"
os.environ["ENCRYPTION_KEY"] = "dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj"

from app.config import Config
from app.database import get_connection, run_migrations


@pytest.fixture
def config():
    """Test config with temp database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    cfg = Config(
        APP_ENV="test",
        APP_BASE_URL="http://test",
        DATABASE_PATH=db_path,
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        STRIPE_WEBHOOK_SECRET="whsec_test_secret",
        COOKIE_DOMAIN="localhost",
        COOKIE_SECURE=False,
        TURNSTILE_SECRET_KEY="",
    )
    yield cfg
    # Cleanup
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


@pytest_asyncio.fixture
async def db(config):
    """Migrated database connection for tests."""
    async with get_connection(config.DATABASE_PATH) as conn:
        await run_migrations(conn)
        yield conn
