"""Phase 5.4: Production readiness tests."""

import os
import sqlite3
import tempfile

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import create_app, VERSION
from app.config import Config
from app.database import get_connection, run_migrations
from app.models.user import create_user
from app.middleware.auth_middleware import create_session


@pytest.fixture
def config():
    """Test config with temp database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    cfg = Config(
        APP_ENV="test",
        DATABASE_PATH=db_path,
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        CORS_ORIGINS="http://localhost:5173,https://app.pressrail.com",
    )
    yield cfg
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass


@pytest.fixture
def app(config):
    return create_app(config)


# ── Health Endpoint ──


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, app, config):
        # Run migrations first
        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["version"] == VERSION
            assert "uptime_seconds" in data
            assert data["services"]["database"] == "ok"

    @pytest.mark.asyncio
    async def test_health_includes_version(self, app, config):
        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/health")
            assert resp.json()["version"] == VERSION


# ── CORS ──


class TestCORS:
    @pytest.mark.asyncio
    async def test_cors_headers_present(self, app, config):
        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.options(
                "/health",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"

    @pytest.mark.asyncio
    async def test_cors_rejects_unknown_origin(self, app, config):
        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.options(
                "/health",
                headers={
                    "Origin": "http://evil.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            # Should not include the evil origin
            assert resp.headers.get("access-control-allow-origin") != "http://evil.com"


# ── Backup Script ──


class TestBackupScript:
    def test_backup_creates_valid_copy(self, config):
        """Backup script creates a valid SQLite copy."""
        from scripts.backup_db import backup_database

        # Create a real DB with data
        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO test_table VALUES (1, 'hello')")
        conn.commit()
        conn.close()

        with tempfile.TemporaryDirectory() as backup_dir:
            backup_path = backup_database(config.DATABASE_PATH, backup_dir)

            # Verify backup exists and is valid
            assert os.path.exists(backup_path)
            assert os.path.getsize(backup_path) > 0

            # Verify data is in backup
            verify = sqlite3.connect(backup_path)
            cursor = verify.execute("SELECT name FROM test_table WHERE id = 1")
            assert cursor.fetchone()[0] == "hello"
            verify.close()

    def test_backup_integrity_check(self, config):
        """Backup passes SQLite integrity check."""
        from scripts.backup_db import backup_database

        conn = sqlite3.connect(config.DATABASE_PATH)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        with tempfile.TemporaryDirectory() as backup_dir:
            backup_path = backup_database(config.DATABASE_PATH, backup_dir)

            verify = sqlite3.connect(backup_path)
            cursor = verify.execute("PRAGMA integrity_check")
            assert cursor.fetchone()[0] == "ok"
            verify.close()

    def test_backup_missing_db_raises(self):
        """Backup raises for nonexistent database."""
        from scripts.backup_db import backup_database

        with tempfile.TemporaryDirectory() as backup_dir:
            with pytest.raises(FileNotFoundError):
                backup_database("/tmp/nonexistent_ghostwriter_db.db", backup_dir)

    def test_cleanup_old_backups(self, config):
        """Cleanup removes old backups, keeps recent ones."""
        from scripts.backup_db import cleanup_old_backups

        with tempfile.TemporaryDirectory() as backup_dir:
            # Create 5 fake backup files
            for i in range(5):
                path = os.path.join(backup_dir, f"ghostwriter_backup_20260{i+1}01_000000.db")
                with open(path, "w") as f:
                    f.write("fake")

            removed = cleanup_old_backups(backup_dir, keep=2)
            assert removed == 3

            remaining = list(os.listdir(backup_dir))
            assert len(remaining) == 2


# ── SPA Fallback ──


class TestSPAFallback:
    @pytest.mark.asyncio
    async def test_spa_fallback_serves_index(self, config):
        """When frontend/dist exists with index.html, non-API 404s serve it."""
        frontend_dist = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "frontend", "dist")

        if not os.path.exists(os.path.join(frontend_dist, "index.html")):
            pytest.skip("frontend/dist not built")

        # Recreate app with frontend dist present
        app = create_app(config)
        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/dashboard")
            assert resp.status_code == 200
            assert "<!doctype html>" in resp.text.lower() or "<html" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_api_routes_not_affected_by_spa(self, app, config):
        """API routes still return proper responses, not SPA fallback."""
        async with get_connection(config.DATABASE_PATH) as db:
            await run_migrations(db)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/settings")
            # Should get 401 (not authenticated), not SPA html
            assert resp.status_code == 401
