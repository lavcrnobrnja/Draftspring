"""Tests for real Ghost integration (Task 3.3).

Mock-based tests verify JWT format, image upload, post creation.
Live tests skipped without TEST_LIVE_APIS.
"""

import json
import os
import time
from unittest.mock import AsyncMock, patch, MagicMock

import jwt
import pytest
import httpx

from app.services.ghost import (
    generate_ghost_jwt,
    validate_ghost_connection,
    upload_image_to_ghost,
    create_ghost_post,
    check_duplicate_post,
)


class TestGhostJWT:
    def test_jwt_structure(self):
        token = generate_ghost_jwt("abc123:aabbccddee112233aabbccddee112233")
        decoded = jwt.decode(token, options={"verify_signature": False})
        assert decoded["aud"] == "/admin/"
        assert "iat" in decoded
        assert "exp" in decoded
        assert decoded["exp"] - decoded["iat"] == 300

    def test_jwt_kid_header(self):
        token = generate_ghost_jwt("mykey123:aabbccddee112233aabbccddee112233")
        header = jwt.get_unverified_header(token)
        assert header["kid"] == "mykey123"
        assert header["alg"] == "HS256"

    def test_invalid_key_format(self):
        with pytest.raises(ValueError):
            generate_ghost_jwt("no-colon-here")

    def test_invalid_hex_secret(self):
        with pytest.raises(ValueError):
            generate_ghost_jwt("id:not-hex-at-all!")


class TestUploadImageToGhost:
    @pytest.mark.asyncio
    async def test_upload_success(self):
        mock_response = httpx.Response(
            201,
            json={
                "images": [{
                    "url": "https://blog.example.com/content/images/2026/03/photo.webp",
                    "ref": "photo.webp",
                }]
            },
        )

        with patch("app.services.ghost.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            url = await upload_image_to_ghost(
                ghost_url="https://blog.example.com",
                api_key="key123:aabbccddee112233aabbccddee112233",
                image_data=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
                filename="photo.webp",
            )

        assert url == "https://blog.example.com/content/images/2026/03/photo.webp"

    @pytest.mark.asyncio
    async def test_upload_failure_raises(self):
        mock_response = httpx.Response(401, json={"errors": [{"message": "Unauthorized"}]})

        with patch("app.services.ghost.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            with pytest.raises(Exception, match="Ghost image upload failed"):
                await upload_image_to_ghost(
                    ghost_url="https://blog.example.com",
                    api_key="key123:aabbccddee112233aabbccddee112233",
                    image_data=b"\x00" * 10,
                    filename="test.webp",
                )


class TestCreateGhostPost:
    @pytest.mark.asyncio
    async def test_create_post_success(self):
        mock_response = httpx.Response(
            201,
            json={
                "posts": [{
                    "id": "ghost-post-123",
                    "url": "https://blog.example.com/remote-work/",
                    "slug": "remote-work",
                }]
            },
        )

        with patch("app.services.ghost.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            result = await create_ghost_post(
                ghost_url="https://blog.example.com",
                api_key="key123:aabbccddee112233aabbccddee112233",
                post_data={
                    "title": "Remote Work Guide",
                    "html": "<h1>Guide</h1><p>Content</p>",
                    "status": "published",
                    "slug": "remote-work",
                    "meta_title": "Remote Work Guide",
                    "meta_description": "A guide to remote work.",
                    "tags": [{"name": "#seo-remote-work"}, {"name": "Remote Work"}],
                },
            )

        assert result["id"] == "ghost-post-123"
        assert result["url"] == "https://blog.example.com/remote-work/"

    @pytest.mark.asyncio
    async def test_create_post_failure(self):
        mock_response = httpx.Response(422, json={"errors": [{"message": "Validation error"}]})

        with patch("app.services.ghost.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            with pytest.raises(Exception, match="Ghost post creation failed"):
                await create_ghost_post(
                    ghost_url="https://blog.example.com",
                    api_key="key123:aabbccddee112233aabbccddee112233",
                    post_data={"title": "Test"},
                )


class TestCheckDuplicatePost:
    @pytest.mark.asyncio
    async def test_finds_duplicate(self):
        mock_response = httpx.Response(
            200,
            json={
                "posts": [{
                    "id": "existing-post",
                    "slug": "remote-work",
                    "url": "https://blog.example.com/remote-work/",
                    "published_at": "2026-03-17T20:00:00.000Z",
                }]
            },
        )

        with patch("app.services.ghost.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            result = await check_duplicate_post(
                ghost_url="https://blog.example.com",
                api_key="key123:aabbccddee112233aabbccddee112233",
                slug="remote-work",
            )

        assert result is not None
        assert result["id"] == "existing-post"

    @pytest.mark.asyncio
    async def test_no_duplicate(self):
        mock_response = httpx.Response(200, json={"posts": []})

        with patch("app.services.ghost.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            result = await check_duplicate_post(
                ghost_url="https://blog.example.com",
                api_key="key123:aabbccddee112233aabbccddee112233",
                slug="nonexistent-post",
            )

        assert result is None


@pytest.mark.skipif(
    not os.environ.get("TEST_LIVE_APIS"),
    reason="No live API keys — set TEST_LIVE_APIS=true to run",
)
class TestLiveGhost:
    @pytest.mark.asyncio
    async def test_live_validate_connection(self):
        from app.config import Config
        cfg = Config()
        result = await validate_ghost_connection(cfg.GHOST_URL, cfg.GHOST_ADMIN_API_KEY)
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_live_upload_image(self):
        from app.config import Config
        cfg = Config()
        url = await upload_image_to_ghost(
            ghost_url=cfg.GHOST_URL,
            api_key=cfg.GHOST_ADMIN_API_KEY,
            image_data=b"\x89PNG" + b"\x00" * 100,
            filename="test-upload.png",
        )
        assert "http" in url
