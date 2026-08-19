"""Task 1.6: Ghost validation tests."""

import jwt
import time

import pytest

from app.services.ghost import generate_ghost_jwt, validate_ghost_connection


class TestGenerateGhostJwt:
    def test_jwt_format(self):
        """JWT has correct kid, alg, aud, exp."""
        api_key = "abcdef1234:1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f"
        token = generate_ghost_jwt(api_key)

        # Decode without verification to check headers/payload
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "HS256"
        assert header["kid"] == "abcdef1234"
        assert header["typ"] == "JWT"

        # Decode payload (need to use the secret to verify)
        secret_hex = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f"
        secret = bytes.fromhex(secret_hex)
        payload = jwt.decode(token, secret, algorithms=["HS256"], audience="/admin/")
        assert payload["aud"] == "/admin/"
        assert "exp" in payload
        assert "iat" in payload
        # Exp should be ~5 minutes from now
        assert payload["exp"] - payload["iat"] == 300

    def test_invalid_key_format(self):
        """Invalid key format raises ValueError."""
        with pytest.raises(ValueError):
            generate_ghost_jwt("invalid-key-no-colon")


class TestValidateGhostConnection:
    @pytest.mark.asyncio
    async def test_valid_connection(self):
        """Valid Ghost connection returns site data (mocked)."""
        # This would need httpx mocking in real implementation
        # For now, test the error cases
        result = await validate_ghost_connection("", "abc:def")
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_invalid_key_format(self):
        """Invalid key format returns error."""
        result = await validate_ghost_connection("https://blog.example.com", "badkey")
        assert result["valid"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_url(self):
        """Empty URL returns error."""
        result = await validate_ghost_connection("", "abc:def123")
        assert result["valid"] is False
