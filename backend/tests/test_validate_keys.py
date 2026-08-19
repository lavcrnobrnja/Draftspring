"""Tests for key validation script (Task 3.4)."""

import importlib
import pytest
from unittest.mock import AsyncMock, patch

from app.config import Config

# Import the script as a module
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import validate_keys
from validate_keys import (
    validate_openai,
    validate_gemini,
    validate_anthropic,
    validate_resend,
    validate_stripe,
    validate_storage,
    validate_image_gen,
)


@pytest.fixture
def config():
    return Config(
        APP_ENV="test",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        OPENAI_API_KEY="sk-test",
        GEMINI_API_KEY="gem-test",
        ANTHROPIC_API_KEY="ant-test",
        RESEND_API_KEY="re-test",
        STRIPE_SECRET_KEY="sk_test_stripe",
        STORAGE_PROVIDER="local",
    )


@pytest.fixture
def empty_config():
    return Config(
        APP_ENV="test",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        OPENAI_API_KEY="",
        GEMINI_API_KEY="",
        ANTHROPIC_API_KEY="",
        RESEND_API_KEY="",
        STRIPE_SECRET_KEY="",
        STORAGE_PROVIDER="local",
    )


def _mock_client(response):
    """Create a mock httpx.AsyncClient context manager."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=response)
    mock_client.post = AsyncMock(return_value=response)
    return mock_client


class TestValidateOpenAI:
    @pytest.mark.asyncio
    async def test_missing_key(self, empty_config):
        ok, msg = await validate_openai(empty_config)
        assert ok is False
        assert "not set" in msg

    @pytest.mark.asyncio
    async def test_success(self, config):
        import httpx as httpx_mod
        mock_response = httpx_mod.Response(200, json={"data": []})
        mock = _mock_client(mock_response)
        with patch.object(validate_keys.httpx, "AsyncClient", return_value=mock):
            ok, msg = await validate_openai(config)
        assert ok is True

    @pytest.mark.asyncio
    async def test_failure(self, config):
        import httpx as httpx_mod
        mock_response = httpx_mod.Response(401, json={"error": "Invalid"})
        mock = _mock_client(mock_response)
        with patch.object(validate_keys.httpx, "AsyncClient", return_value=mock):
            ok, msg = await validate_openai(config)
        assert ok is False


class TestValidateGemini:
    @pytest.mark.asyncio
    async def test_missing_key(self, empty_config):
        ok, msg = await validate_gemini(empty_config)
        assert ok is False

    @pytest.mark.asyncio
    async def test_success(self, config):
        import httpx as httpx_mod
        mock_response = httpx_mod.Response(200, json={"models": []})
        mock = _mock_client(mock_response)
        with patch.object(validate_keys.httpx, "AsyncClient", return_value=mock):
            ok, msg = await validate_gemini(config)
        assert ok is True


class TestValidateAnthropic:
    @pytest.mark.asyncio
    async def test_missing_key(self, empty_config):
        ok, msg = await validate_anthropic(empty_config)
        assert ok is False

    @pytest.mark.asyncio
    async def test_success(self, config):
        import httpx as httpx_mod
        mock_response = httpx_mod.Response(200, json={"content": [{"text": "hi"}]})
        mock = _mock_client(mock_response)
        with patch.object(validate_keys.httpx, "AsyncClient", return_value=mock):
            ok, msg = await validate_anthropic(config)
        assert ok is True


class TestValidateResend:
    @pytest.mark.asyncio
    async def test_missing_key(self, empty_config):
        ok, msg = await validate_resend(empty_config)
        assert ok is False

    @pytest.mark.asyncio
    async def test_success(self, config):
        import httpx as httpx_mod
        mock_response = httpx_mod.Response(200, json={"data": []})
        mock = _mock_client(mock_response)
        with patch.object(validate_keys.httpx, "AsyncClient", return_value=mock):
            ok, msg = await validate_resend(config)
        assert ok is True


class TestValidateStripe:
    @pytest.mark.asyncio
    async def test_missing_key(self, empty_config):
        ok, msg = await validate_stripe(empty_config)
        assert ok is False

    @pytest.mark.asyncio
    async def test_success(self, config):
        import httpx as httpx_mod
        mock_response = httpx_mod.Response(200, json={"available": []})
        mock = _mock_client(mock_response)
        with patch.object(validate_keys.httpx, "AsyncClient", return_value=mock):
            ok, msg = await validate_stripe(config)
        assert ok is True


class TestValidateStorage:
    @pytest.mark.asyncio
    async def test_local_always_ok(self, config):
        ok, msg = await validate_storage(config)
        assert ok is True
        assert "local" in msg

    @pytest.mark.asyncio
    async def test_s3_missing_endpoint_or_bucket(self):
        cfg = Config(
            APP_ENV="test",
            ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
            STORAGE_PROVIDER="s3",
            S3_ENDPOINT_URL="",
            S3_ACCESS_KEY_ID="",
            S3_SECRET_ACCESS_KEY="",
            S3_BUCKET_NAME="",
        )
        ok, msg = await validate_storage(cfg)
        assert ok is False
        assert "endpoint" in msg.lower() or "bucket" in msg.lower()


class TestValidateImageGen:
    @pytest.mark.asyncio
    async def test_missing_key(self, empty_config):
        ok, msg = await validate_image_gen(empty_config)
        assert ok is False

    @pytest.mark.asyncio
    async def test_key_present(self, config):
        ok, msg = await validate_image_gen(config)
        assert ok is True
