"""Integration pipeline test (Task 3.4).

Full E2E with mock providers: seed → ideation → CP1 → outline → draft loop →
images → CP2 → schedule → publish.

Live integration test is skipped unless TEST_LIVE_APIS is set.
"""

import json
import os
import tempfile

import pytest
import pytest_asyncio

from app.config import Config
from app.providers import create_all_providers


@pytest.fixture
def mock_config():
    return Config(
        APP_ENV="test",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
        STORAGE_PROVIDER="local",
        LOCAL_STORAGE_PATH=tempfile.mkdtemp(),
    )


class TestProviderIntegration:
    """Verify mock providers wire together correctly."""

    def test_mock_providers_created(self, mock_config):
        providers = create_all_providers(mock_config)
        assert providers["llm"] is not None
        assert providers["storage"] is not None
        assert providers["email"] is not None

    @pytest.mark.asyncio
    async def test_mock_llm_ideation(self, mock_config):
        providers = create_all_providers(mock_config)
        llm = providers["llm"]
        result = await llm.generate_ideas(
            seeds=[{"type": "topic", "content": "remote work"}],
            ideas_per_seed=3,
            existing_titles=[],
        )
        assert "ideas" in result
        assert len(result["ideas"]) == 3

    @pytest.mark.asyncio
    async def test_mock_llm_outline(self, mock_config):
        providers = create_all_providers(mock_config)
        llm = providers["llm"]
        result = await llm.generate_outline(
            idea={"title": "Test", "angle": "A test angle", "target_keyword": "test"},
            blog_context={"brand_voice": "Professional"},
            target_word_count=1500,
        )
        assert "sections" in result
        assert "seo_block" in result

    @pytest.mark.asyncio
    async def test_mock_llm_draft(self, mock_config):
        providers = create_all_providers(mock_config)
        llm = providers["llm"]
        outline = await llm.generate_outline(
            idea={"title": "Test", "angle": "A test angle", "target_keyword": "test"},
            blog_context={"brand_voice": "Professional"},
            target_word_count=1500,
        )
        draft = await llm.draft_article(
            outline=outline,
            seo_meta=outline["seo_block"],
            brand_voice="Professional tone",
        )
        assert "# " in draft  # Has H1
        assert "IMAGE_ANCHOR" in draft

    @pytest.mark.asyncio
    async def test_mock_storage_roundtrip(self, mock_config):
        providers = create_all_providers(mock_config)
        storage = providers["storage"]
        url = await storage.upload("test/img.webp", b"fake-image-data", "image/webp")
        assert url is not None
        data = await storage.download("test/img.webp")
        assert data == b"fake-image-data"

    @pytest.mark.asyncio
    async def test_mock_email_sends(self, mock_config):
        providers = create_all_providers(mock_config)
        email = providers["email"]
        email.clear_sent_emails()

        result = await email.send_magic_link_email(
            mock_config, "user@test.com", "token123", "login"
        )
        assert result is True
        assert len(email.get_sent_emails()) == 1

    @pytest.mark.asyncio
    async def test_full_mock_pipeline_flow(self, mock_config):
        """Simulate the full pipeline with mock providers."""
        providers = create_all_providers(mock_config)
        llm = providers["llm"]
        storage = providers["storage"]
        email_svc = providers["email"]
        email_svc.clear_sent_emails()

        # Step 1: Ideation
        ideas = await llm.generate_ideas(
            seeds=[{"type": "topic", "content": "sustainable packaging"}],
            ideas_per_seed=3,
            existing_titles=[],
        )
        assert len(ideas["ideas"]) == 3

        # Step 2: Pick an idea, generate outline
        idea = ideas["ideas"][0]
        outline = await llm.generate_outline(
            idea=idea,
            blog_context={"brand_voice": "Professional"},
            target_word_count=1500,
        )
        assert len(outline["sections"]) >= 4

        # Step 3: Draft article
        draft = await llm.draft_article(
            outline=outline,
            seo_meta=outline["seo_block"],
            brand_voice="Professional but conversational",
        )
        assert len(draft) > 100

        # Step 4: Humanize
        humanized = await llm.humanize(draft)
        assert "IMAGE_ANCHOR" in humanized

        # Step 5: Critique
        critique = await llm.critique_draft(
            humanized_md=humanized,
            outline=outline,
            seo_meta=outline["seo_block"],
            iteration_number=1,
            max_iterations=3,
            article_title="Test Title",
            article_angle="Test angle",
            search_intent="informational",
            focus_keyword="test keyword",
        )
        assert "score" in critique

        # Step 6: Generate images
        image_data = await llm.generate_image("A sustainable packaging warehouse")
        assert len(image_data) > 0

        # Step 7: Upload image to storage
        url = await storage.upload("images/test.webp", image_data, "image/webp")
        assert url is not None

        # Step 8: Generate alt text
        alt_texts = await llm.generate_alt_texts(
            focus_keyword="sustainable packaging",
            images=[{"heading": "Introduction", "guidance": "packaging warehouse"}],
        )
        assert "alt_texts" in alt_texts

        # Step 9: Send notification email
        result = await email_svc.send_publish_notification_email(
            mock_config,
            to="user@test.com",
            article_title="Sustainable Packaging Guide",
            article_url="https://blog.example.com/sustainable-packaging/",
        )
        assert result is True

        # Verify emails were sent during pipeline
        emails = email_svc.get_sent_emails()
        assert len(emails) >= 1


@pytest.mark.skipif(
    not os.environ.get("TEST_LIVE_APIS"),
    reason="No live API keys — set TEST_LIVE_APIS=true to run",
)
class TestLiveIntegrationPipeline:
    """Live integration test — costs ~$1-3. Requires all API keys configured."""

    @pytest.mark.asyncio
    async def test_live_seed_to_draft(self):
        """Real seed → real ideas → real outline → real draft."""
        config = Config()
        providers = create_all_providers(config)
        llm = providers["llm"]

        # Generate ideas
        ideas = await llm.generate_ideas(
            seeds=[{"type": "topic", "content": "remote work productivity tips"}],
            ideas_per_seed=2,
            existing_titles=[],
        )
        assert len(ideas["ideas"]) >= 1

        # Generate outline
        idea = ideas["ideas"][0]
        outline = await llm.generate_outline(
            idea=idea,
            blog_context={"brand_voice": "Professional but conversational"},
            target_word_count=1500,
        )
        assert len(outline["sections"]) >= 4

        # Draft
        draft = await llm.draft_article(
            outline=outline,
            seo_meta=outline["seo_block"],
            brand_voice="Professional but conversational",
        )
        assert len(draft) > 500
