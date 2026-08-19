"""Tests for provider factory (Task 3.4)."""

import pytest

from app.config import Config
from app.providers import (
    create_llm_provider,
    create_storage_provider,
    create_email_service,
    create_all_providers,
)


@pytest.fixture
def test_config():
    return Config(
        APP_ENV="test",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        STORAGE_PROVIDER="local",
        LOCAL_STORAGE_PATH="/tmp/ghostwriter-test-storage",
    )


@pytest.fixture
def prod_config():
    return Config(
        APP_ENV="production",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        OPENAI_API_KEY="sk-test",
        GEMINI_API_KEY="gem-test",
        ANTHROPIC_API_KEY="ant-test",
        STORAGE_PROVIDER="local",
        LOCAL_STORAGE_PATH="/tmp/ghostwriter-test-storage",
    )


@pytest.fixture
def s3_config():
    return Config(
        APP_ENV="production",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        STORAGE_PROVIDER="s3",
        S3_ENDPOINT_URL="https://s3.example.com",
        S3_ACCESS_KEY_ID="access-key",
        S3_SECRET_ACCESS_KEY="secret-key",
        S3_BUCKET_NAME="test-bucket",
        S3_PUBLIC_URL_PREFIX="https://cdn.example.com",
    )


class TestCreateLLMProvider:
    def test_test_env_returns_mock(self, test_config):
        from app.llm.mock import MockLLM
        provider = create_llm_provider(test_config)
        assert isinstance(provider, MockLLM)

    def test_dev_env_returns_mock(self):
        config = Config(
            APP_ENV="development",
            ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        )
        from app.llm.mock import MockLLM
        provider = create_llm_provider(config)
        assert isinstance(provider, MockLLM)

    def test_production_env_returns_live(self, prod_config):
        from app.llm.live import LiveLLM
        provider = create_llm_provider(prod_config)
        assert isinstance(provider, LiveLLM)


class TestCreateStorageProvider:
    def test_local_storage(self, test_config):
        from app.storage.local import LocalStorage
        provider = create_storage_provider(test_config)
        assert isinstance(provider, LocalStorage)

    def test_s3_storage(self, s3_config):
        from app.storage.s3 import S3Storage
        provider = create_storage_provider(s3_config)
        assert isinstance(provider, S3Storage)


class TestCreateEmailService:
    def test_returns_email_module(self, test_config):
        from app.services import email
        service = create_email_service(test_config)
        assert service is email
        assert hasattr(service, "send_magic_link_email")
        assert hasattr(service, "send_revision_confirmation_email")
        assert hasattr(service, "send_publish_notification_email")


class TestCreateAllProviders:
    def test_returns_all_keys(self, test_config):
        providers = create_all_providers(test_config)
        assert "llm" in providers
        assert "storage" in providers
        assert "email" in providers
        assert "config" in providers

    def test_test_env_uses_mocks(self, test_config):
        from app.llm.mock import MockLLM
        from app.storage.local import LocalStorage
        providers = create_all_providers(test_config)
        assert isinstance(providers["llm"], MockLLM)
        assert isinstance(providers["storage"], LocalStorage)

    def test_config_passed_through(self, test_config):
        providers = create_all_providers(test_config)
        assert providers["config"] is test_config
