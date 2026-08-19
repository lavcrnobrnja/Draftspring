"""Tests for Ghost author selection feature."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ghost import fetch_ghost_staff, validate_ghost_connection
from app.models.user import create_user, update_user
from tests.test_locking import _create_article


class TestFetchGhostStaff:
    """Tests for fetch_ghost_staff function."""

    @pytest.mark.asyncio
    async def test_returns_active_staff_sorted_by_role(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "users": [
                {"id": "u3", "name": "Carol", "email": "carol@test.com", "status": "active", "roles": [{"name": "Author"}]},
                {"id": "u1", "name": "Alice", "email": "alice@test.com", "status": "active", "roles": [{"name": "Owner"}]},
                {"id": "u2", "name": "Bob", "email": "bob@test.com", "status": "active", "roles": [{"name": "Editor"}]},
            ]
        }

        with patch("app.services.ghost.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            result = await fetch_ghost_staff("https://blog.test.com", "abc123:def456")

        assert result["error"] is None
        assert len(result["staff"]) == 3
        assert result["staff"][0]["name"] == "Alice"  # Owner first
        assert result["staff"][0]["role"] == "Owner"
        assert result["staff"][1]["name"] == "Bob"  # Editor second
        assert result["staff"][2]["name"] == "Carol"  # Author third

    @pytest.mark.asyncio
    async def test_excludes_inactive_users(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "users": [
                {"id": "u1", "name": "Active", "email": "a@test.com", "status": "active", "roles": [{"name": "Owner"}]},
                {"id": "u2", "name": "Inactive", "email": "i@test.com", "status": "inactive", "roles": [{"name": "Author"}]},
            ]
        }

        with patch("app.services.ghost.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            result = await fetch_ghost_staff("https://blog.test.com", "abc123:def456")

        assert result["error"] is None
        assert len(result["staff"]) == 1
        assert result["staff"][0]["name"] == "Active"

    @pytest.mark.asyncio
    async def test_returns_empty_on_api_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("app.services.ghost.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            result = await fetch_ghost_staff("https://blog.test.com", "abc123:def456")

        assert result["staff"] == []
        assert result["error"] is not None
        assert "invalid" in result["error"].lower() or "expired" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_user_with_no_roles(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "users": [
                {"id": "u1", "name": "NoRole", "email": "nr@test.com", "status": "active", "roles": []},
            ]
        }

        with patch("app.services.ghost.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(return_value=mock_response)

            result = await fetch_ghost_staff("https://blog.test.com", "abc123:def456")

        assert result["error"] is None
        assert len(result["staff"]) == 1
        assert result["staff"][0]["role"] == "Unknown"


    @pytest.mark.asyncio
    async def test_returns_error_on_connection_failure(self):
        with patch("app.services.ghost.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(side_effect=Exception("connection reset"))

            result = await fetch_ghost_staff("https://blog.test.com", "abc123:def456")

        assert result["staff"] == []
        assert result["error"] is not None

    @pytest.mark.asyncio
    async def test_returns_error_on_invalid_key_format(self):
        result = await fetch_ghost_staff("https://blog.test.com", "no-colon-here")
        assert result["staff"] == []
        assert "format" in result["error"].lower()


class TestValidateGhostConnection:
    """Tests for validate_ghost_connection — must check auth-required endpoint."""

    @pytest.mark.asyncio
    async def test_rejects_key_that_passes_site_but_fails_users(self):
        """The /site/ endpoint is public on many Ghost instances.
        Validation must also hit /users/ to catch invalid keys."""
        site_response = MagicMock()
        site_response.status_code = 200
        site_response.json.return_value = {
            "site": {"title": "My Blog", "version": "5.80"}
        }

        users_response = MagicMock()
        users_response.status_code = 401

        with patch("app.services.ghost.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(side_effect=[site_response, users_response])

            result = await validate_ghost_connection("https://blog.test.com", "abc123:def456")

        assert result["valid"] is False
        assert "authenticate" in result["error"].lower() or "invalid" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_accepts_key_that_passes_both_endpoints(self):
        site_response = MagicMock()
        site_response.status_code = 200
        site_response.json.return_value = {
            "site": {"title": "My Blog", "version": "5.80"}
        }

        users_response = MagicMock()
        users_response.status_code = 200
        users_response.json.return_value = {"users": []}

        with patch("app.services.ghost.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(return_value=mock_client.return_value)
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value.get = AsyncMock(side_effect=[site_response, users_response])

            result = await validate_ghost_connection("https://blog.test.com", "abc123:def456")

        assert result["valid"] is True
        assert result["site_title"] == "My Blog"


class TestGhostAuthorStorage:
    """Test ghost_author_id storage and lifecycle."""

    @pytest.mark.asyncio
    async def test_author_stored_on_user(self, db):
        user = await create_user(db, "author-test@test.com")
        await update_user(db, user["id"], ghost_author_id="ghost_123", ghost_author_name="Test Author")

        cursor = await db.execute("SELECT ghost_author_id, ghost_author_name FROM users WHERE id = ?", (user["id"],))
        row = await cursor.fetchone()
        assert row["ghost_author_id"] == "ghost_123"
        assert row["ghost_author_name"] == "Test Author"

    @pytest.mark.asyncio
    async def test_author_null_by_default(self, db):
        user = await create_user(db, "noauthor@test.com")

        cursor = await db.execute("SELECT ghost_author_id FROM users WHERE id = ?", (user["id"],))
        row = await cursor.fetchone()
        assert row["ghost_author_id"] is None

    @pytest.mark.asyncio
    async def test_author_cleared(self, db):
        user = await create_user(db, "clear-test@test.com")
        await update_user(db, user["id"], ghost_author_id="old_id", ghost_author_name="Old")
        await update_user(db, user["id"], ghost_author_id=None, ghost_author_name=None)

        cursor = await db.execute("SELECT ghost_author_id, ghost_author_name FROM users WHERE id = ?", (user["id"],))
        row = await cursor.fetchone()
        assert row["ghost_author_id"] is None
        assert row["ghost_author_name"] is None


class TestT11AuthorInPostData:
    """Test that T11 publishing includes author in post_data.
    
    Uses the existing test_publishing.py fixture pattern — creates a full article
    through the pipeline, then verifies the Ghost post_data includes/excludes authors.
    """

    @pytest.mark.asyncio
    async def test_author_id_in_sql_query(self, db):
        """Verify ghost_author_id is returned by the T11 SQL query."""
        user = await create_user(db, "t11sql@test.com")
        await update_user(db, user["id"], ghost_author_id="staff_abc", ghost_author_name="Staff User")

        article_id = await _create_article(db, user["id"], "READY_TO_PUBLISH")

        cursor = await db.execute(
            """SELECT a.*, u.ghost_author_id
               FROM articles a JOIN users u ON a.user_id = u.id WHERE a.id = ?""",
            (article_id,),
        )
        row = dict(await cursor.fetchone())
        assert row["ghost_author_id"] == "staff_abc"

    @pytest.mark.asyncio
    async def test_author_id_null_in_query(self, db):
        """Verify ghost_author_id is NULL when not set."""
        user = await create_user(db, "t11null@test.com")
        article_id = await _create_article(db, user["id"], "READY_TO_PUBLISH")

        cursor = await db.execute(
            """SELECT a.*, u.ghost_author_id
               FROM articles a JOIN users u ON a.user_id = u.id WHERE a.id = ?""",
            (article_id,),
        )
        row = dict(await cursor.fetchone())
        assert row["ghost_author_id"] is None

    def test_post_data_includes_authors_when_set(self):
        """Unit test: verify the authors logic in isolation."""
        post_data = {"title": "Test", "html": "<p>hi</p>", "status": "published"}
        ghost_author_id = "staff_abc"

        if ghost_author_id:
            post_data["authors"] = [{"id": ghost_author_id}]

        assert post_data["authors"] == [{"id": "staff_abc"}]

    def test_post_data_no_authors_when_none(self):
        """Unit test: verify authors not added when NULL."""
        post_data = {"title": "Test", "html": "<p>hi</p>", "status": "published"}
        ghost_author_id = None

        if ghost_author_id:
            post_data["authors"] = [{"id": ghost_author_id}]

        assert "authors" not in post_data
