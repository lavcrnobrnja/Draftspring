"""Tests for real email service (Task 3.3).

Mock-based tests verify email templates and Resend integration.
Live tests skipped without TEST_LIVE_APIS.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
import httpx

from app.config import Config
from app.services.email import (
    send_magic_link_email,
    send_revision_confirmation_email,
    send_publish_notification_email,
    get_sent_emails,
    clear_sent_emails,
)


@pytest.fixture
def test_config():
    return Config(
        APP_ENV="test",
        APP_BASE_URL="http://localhost:8000",
        RESEND_API_KEY="re_test_key",
        EMAIL_FROM_ADDRESS="content@example.com",
        EMAIL_FROM_NAME="DraftSpring",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
    )


@pytest.fixture
def prod_config():
    return Config(
        APP_ENV="production",
        APP_BASE_URL="https://app.ghostwriter.io",
        RESEND_API_KEY="re_test_key",
        EMAIL_FROM_ADDRESS="content@ghostwriter.io",
        EMAIL_FROM_NAME="DraftSpring",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
    )


@pytest.fixture(autouse=True)
def clean_emails():
    clear_sent_emails()
    yield
    clear_sent_emails()


class TestMagicLinkEmail:
    @pytest.mark.asyncio
    async def test_login_email_test_mode(self, test_config):
        result = await send_magic_link_email(
            test_config, "user@example.com", "token123", "login"
        )
        assert result is True
        emails = get_sent_emails()
        assert len(emails) == 1
        assert emails[0]["to"] == "user@example.com"
        assert "Sign in" in emails[0]["subject"] or "Login" in emails[0]["subject"]
        assert "token123" in emails[0]["url"]

    @pytest.mark.asyncio
    async def test_cp1_email_test_mode(self, test_config):
        result = await send_magic_link_email(
            test_config, "user@example.com", "cp1token", "checkpoint_1", reference_id="batch-123"
        )
        assert result is True
        emails = get_sent_emails()
        assert emails[0]["purpose"] == "checkpoint_1"
        assert emails[0]["reference_id"] == "batch-123"

    @pytest.mark.asyncio
    async def test_cp2_email_test_mode(self, test_config):
        result = await send_magic_link_email(
            test_config, "user@example.com", "cp2token", "checkpoint_2"
        )
        assert result is True
        emails = get_sent_emails()
        assert "review" in emails[0]["subject"].lower()

    @pytest.mark.asyncio
    async def test_production_calls_resend(self, prod_config):
        mock_response = httpx.Response(200, json={"id": "email-123"})

        with patch("app.services.email.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            MockClient.return_value = mock_client

            result = await send_magic_link_email(
                prod_config, "user@example.com", "token", "login"
            )

        assert result is True
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "resend.com" in call_args[0][0]


class TestRevisionConfirmationEmail:
    @pytest.mark.asyncio
    async def test_sends_in_test_mode(self, test_config):
        result = await send_revision_confirmation_email(
            test_config,
            to="user@example.com",
            article_title="Remote Work Guide",
        )
        assert result is True
        emails = get_sent_emails()
        assert len(emails) == 1
        assert "Revision" in emails[0]["subject"]
        assert emails[0]["to"] == "user@example.com"


class TestPublishNotificationEmail:
    @pytest.mark.asyncio
    async def test_sends_in_test_mode(self, test_config):
        result = await send_publish_notification_email(
            test_config,
            to="user@example.com",
            article_title="Remote Work Guide",
            article_url="https://blog.example.com/remote-work/",
        )
        assert result is True
        emails = get_sent_emails()
        assert len(emails) == 1
        assert "Published" in emails[0]["subject"]
        assert emails[0]["to"] == "user@example.com"


@pytest.mark.skipif(
    not os.environ.get("TEST_LIVE_APIS"),
    reason="No live API keys — set TEST_LIVE_APIS=true to run",
)
class TestLiveEmail:
    @pytest.mark.asyncio
    async def test_live_login_email(self):
        cfg = Config()
        result = await send_magic_link_email(cfg, cfg.ADMIN_EMAILS.split(",")[0], "test-token", "login")
        assert result is True
