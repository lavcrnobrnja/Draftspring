"""Tests for LiveLLM with mocked HTTP calls (Task 3.1).

Live tests are skipped unless TEST_LIVE_APIS is set.
Mock-based tests verify schema-valid output, token extraction, retry logic.
"""

import json
import os
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx

from app.llm.live import LiveLLM
from app.config import Config


@pytest.fixture
def config():
    return Config(
        APP_ENV="test",
        OPENAI_API_KEY="sk-test-openai-key",
        OPENAI_MODEL_ID="gpt-5.4",
        OPENAI_BASE_URL="https://api.openai.com/v1",
        GEMINI_API_KEY="test-gemini-key",
        GEMINI_MODEL_ID="gemini-2.5-pro-deep-research",
        ANTHROPIC_API_KEY="test-anthropic-key",
        ANTHROPIC_MODEL_ID="claude-sonnet-4-6",
        ENCRYPTION_KEY="dGVzdC1lbmNyeXB0aW9uLWtleS0xMjM0NTY3ODkwYWJj",
    )


@pytest.fixture
def llm(config):
    return LiveLLM(config)


def _openai_response(content: str, input_tokens=100, output_tokens=200):
    """Build a mock OpenAI chat completion response."""
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        },
    )


def _anthropic_response(content: str, input_tokens=150, output_tokens=300):
    """Build a mock Anthropic messages response."""
    return httpx.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": content}],
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        },
    )


def _gemini_response(content: str):
    """Build a mock Gemini generateContent response."""
    return httpx.Response(
        200,
        json={
            "candidates": [{
                "content": {"parts": [{"text": content}], "role": "model"},
                "finishReason": "STOP",
            }],
            "usageMetadata": {
                "promptTokenCount": 120,
                "candidatesTokenCount": 250,
                "totalTokenCount": 370,
            },
        },
    )


def _nano_banana_response():
    """Build a mock image generation response."""
    return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)


# ─── Ideation (OpenAI GPT-5.4) ───

class TestGenerateIdeas:
    @pytest.mark.asyncio
    async def test_returns_valid_ideas(self, llm):
        ideas_json = json.dumps({
            "ideas": [
                {
                    "seed_index": 0,
                    "title": "Why Remote Work Is Here to Stay",
                    "angle": "Exploring the permanent shift to remote work post-pandemic.",
                    "target_keyword": "remote-work-future",
                    "estimated_search_volume": "medium",
                },
                {
                    "seed_index": 0,
                    "title": "Building a Remote Team Culture",
                    "angle": "How to maintain culture when everyone is distributed.",
                    "target_keyword": "remote-team-culture",
                    "estimated_search_volume": "low",
                },
                {
                    "seed_index": 0,
                    "title": "Remote Work Tools That Actually Work",
                    "angle": "A review of the best remote work software.",
                    "target_keyword": "remote-work-tools",
                    "estimated_search_volume": "high",
                },
            ]
        })

        with patch.object(llm, "_openai_chat", new_callable=AsyncMock, return_value=(ideas_json, {"input_tokens": 100, "output_tokens": 200})):
            result = await llm.generate_ideas(
                seeds=[{"content": "remote work", "seed_type": "topic"}],
                ideas_per_seed=3,
                existing_titles=[],
            )

        assert "ideas" in result
        assert len(result["ideas"]) == 3
        for idea in result["ideas"]:
            assert "title" in idea
            assert "angle" in idea
            assert "target_keyword" in idea
            assert idea["estimated_search_volume"] in ("low", "medium", "high")

    @pytest.mark.asyncio
    async def test_token_counts_extracted(self, llm):
        ideas_json = json.dumps({
            "ideas": [{
                "seed_index": 0,
                "title": "Test",
                "angle": "Test angle",
                "target_keyword": "test",
                "estimated_search_volume": "low",
            }]
        })

        with patch.object(llm, "_openai_chat", new_callable=AsyncMock, return_value=(ideas_json, {"input_tokens": 100, "output_tokens": 200})):
            result = await llm.generate_ideas(
                seeds=[{"content": "test", "seed_type": "topic"}],
                ideas_per_seed=1,
                existing_titles=[],
            )

        assert result.get("_usage", {}).get("input_tokens") == 100
        assert result.get("_usage", {}).get("output_tokens") == 200


# ─── Outline (Gemini Deep Research) ───

class TestGenerateOutline:
    @pytest.mark.asyncio
    async def test_returns_valid_outline(self, llm):
        outline_json = json.dumps({
            "working_title": "Why Remote Work Is Here to Stay",
            "thesis": "Remote work has become permanent.",
            "target_word_count": 1500,
            "sections": [
                {"heading": "The Shift", "subheadings": ["Pre-pandemic", "Post-pandemic"], "key_points": ["Point 1", "Point 2"], "research_notes": "Notes", "image_needed": True, "image_guidance": "A home office setup with dual monitors"},
                {"heading": "Benefits", "subheadings": ["Flexibility"], "key_points": ["Better work-life balance"], "research_notes": "Notes", "image_needed": False, "image_guidance": ""},
                {"heading": "Challenges", "subheadings": ["Isolation"], "key_points": ["Loneliness"], "research_notes": "Notes", "image_needed": True, "image_guidance": "A person working alone at kitchen table"},
                {"heading": "Tools", "subheadings": ["Software"], "key_points": ["Slack, Zoom"], "research_notes": "Notes", "image_needed": False, "image_guidance": ""},
                {"heading": "Future", "subheadings": ["Hybrid"], "key_points": ["Trend data"], "research_notes": "Notes", "image_needed": True, "image_guidance": "Futuristic office with VR headsets"},
            ],
            "seo": {
                "focus_keyword": "remote work",
                "secondary_keywords": ["wfh", "telecommuting"],
                "meta_title": "Why Remote Work Is Here to Stay in 2026",
                "meta_description": "Discover why remote work has become a permanent fixture and how to thrive in a distributed work environment.",
                "suggested_slug": "remote-work-here-to-stay",
            },
        })

        with patch.object(llm, "_gemini_generate", new_callable=AsyncMock, return_value=(outline_json, {"input_tokens": 120, "output_tokens": 250})):
            result = await llm.generate_outline(
                idea={"title": "Remote Work", "target_keyword": "remote work", "angle": "Future of remote"},
                blog_context={"ghost_url": "https://blog.example.com"},
                target_word_count=1500,
            )

        assert "sections" in result
        assert len(result["sections"]) == 5
        assert "seo" in result
        assert len(result["seo"]["meta_title"]) <= 60
        assert len(result["seo"]["meta_description"]) <= 155
        images_needed = [s for s in result["sections"] if s["image_needed"]]
        assert len(images_needed) >= 2


# ─── Drafting (OpenAI GPT-5.4) ───

class TestDraftArticle:
    @pytest.mark.asyncio
    async def test_returns_markdown(self, llm):
        draft = "# Remote Work Guide\n\nIn this article about remote work...\n\n## The Shift\n\nRemote work changed...\n\n[IMAGE_ANCHOR:0]\n\n## Benefits\n\nMany benefits of remote work...\n\n## Conclusion\n\nRemote work is here to stay."

        with patch.object(llm, "_openai_chat", new_callable=AsyncMock, return_value=(draft, {"input_tokens": 500, "output_tokens": 1000})):
            result = await llm.draft_article(
                outline={"working_title": "Remote Work", "sections": []},
                seo_meta={"focus_keyword": "remote work"},
                brand_voice="Professional",
            )

        assert isinstance(result, str)
        assert "# " in result
        assert "## " in result


# ─── Humanize (Anthropic Claude Sonnet 4.6) ───

class TestHumanize:
    @pytest.mark.asyncio
    async def test_returns_humanized_text(self, llm):
        humanized = "# Remote Work: What Actually Changed\n\nLook, remote work isn't going anywhere...\n\n[IMAGE_ANCHOR:0]\n\n## The Real Shift\n\nHere's the thing..."

        with patch.object(llm, "_anthropic_message", new_callable=AsyncMock, return_value=(humanized, {"input_tokens": 400, "output_tokens": 500})):
            result = await llm.humanize("# Remote Work Guide\n\nIn this article...\n\n[IMAGE_ANCHOR:0]\n\n## The Shift\n\n...")

        assert isinstance(result, str)
        assert "[IMAGE_ANCHOR:0]" in result


# ─── Critique (Anthropic Claude Sonnet 4.6) ───

class TestCritiqueDraft:
    CRITIQUE_KWARGS = dict(
        article_title="Test Title",
        article_angle="Test angle",
        search_intent="informational",
        focus_keyword="test keyword",
    )

    @pytest.mark.asyncio
    async def test_returns_valid_critique(self, llm):
        critique_json = json.dumps({
            "score": 7,
            "verdict": "approved",
            "summary": "The article is solid with clear structure and good keyword usage.",
            "issues": [],
            "seo_check": {
                "meta_fix_suggestion": None,
            },
        })

        with patch.object(llm, "_anthropic_message", new_callable=AsyncMock, return_value=(critique_json, {"input_tokens": 300, "output_tokens": 400})):
            result = await llm.critique_draft(
                humanized_md="# Test\n\n...",
                outline={"sections": []},
                seo_meta={"focus_keyword": "test"},
                iteration_number=1,
                max_iterations=3,
                **self.CRITIQUE_KWARGS,
            )

        assert "score" in result
        assert result["score"] == 7
        assert result["verdict"] in ("approved", "revision_needed")
        assert "seo_check" in result
        assert "summary" in result

    @pytest.mark.asyncio
    async def test_revision_needed_has_issues(self, llm):
        critique_json = json.dumps({
            "score": 5,
            "verdict": "revision_needed",
            "summary": "The draft has a weak opening and SEO issues that need addressing.",
            "issues": [{
                "severity": "major",
                "location": "Introduction",
                "description": "Weak opening",
                "fix": "Add a compelling statistic or question to hook the reader immediately.",
            }],
            "seo_check": {
                "meta_fix_suggestion": "Rewrite to focus on the main benefit of test keyword for better click-through rates in search results.",
            },
        })

        with patch.object(llm, "_anthropic_message", new_callable=AsyncMock, return_value=(critique_json, {"input_tokens": 300, "output_tokens": 400})):
            result = await llm.critique_draft(
                humanized_md="# Test\n\n...",
                outline={"sections": []},
                seo_meta={"focus_keyword": "test"},
                iteration_number=1,
                max_iterations=3,
                **self.CRITIQUE_KWARGS,
            )

        assert result["verdict"] == "revision_needed"
        assert len(result["issues"]) > 0
        assert len(result["issues"][0]["fix"]) > 20


# ─── Alt Text (OpenAI GPT-5.4) ───

class TestGenerateAltTexts:
    @pytest.mark.asyncio
    async def test_returns_alt_texts(self, llm):
        alt_json = json.dumps({
            "alt_texts": [
                "A home office setup with dual monitors and ergonomic chair",
                "Person working remotely from a coffee shop with laptop",
            ]
        })

        with patch.object(llm, "_openai_chat", new_callable=AsyncMock, return_value=(alt_json, {"input_tokens": 50, "output_tokens": 80})):
            result = await llm.generate_alt_texts(
                focus_keyword="remote work",
                images=[
                    {"section_heading": "Home Office", "image_guidance": "dual monitor setup"},
                    {"section_heading": "Mobile Work", "image_guidance": "coffee shop laptop"},
                ],
            )

        assert "alt_texts" in result
        assert len(result["alt_texts"]) == 2


# ─── Image Generation (Nano Banana) ───

class TestGenerateImage:
    @pytest.mark.asyncio
    async def test_returns_bytes(self, llm):
        fake_image = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        with patch.object(llm, "_generate_image_raw", new_callable=AsyncMock, return_value=fake_image):
            result = await llm.generate_image("A remote worker at a desk")

        assert isinstance(result, bytes)
        assert len(result) > 0


# ─── Retry Logic ───

class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_429_retries_with_retry_after(self, llm):
        """429 should retry after Retry-After header."""
        resp_429 = httpx.Response(429, headers={"Retry-After": "0"})
        resp_ok = _openai_response(json.dumps({"ideas": [{"seed_index": 0, "title": "T", "angle": "A", "target_keyword": "k", "estimated_search_volume": "low"}]}))

        call_count = 0

        async def mock_send(request, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resp_429
            return resp_ok

        with patch.object(llm._openai_client, "send", side_effect=mock_send):
            result = await llm.generate_ideas(
                seeds=[{"content": "test", "seed_type": "topic"}],
                ideas_per_seed=1,
                existing_titles=[],
            )

        assert call_count == 2
        assert "ideas" in result

    @pytest.mark.asyncio
    async def test_5xx_retries_with_backoff(self, llm):
        """5xx should retry with exponential backoff."""
        resp_500 = httpx.Response(500)
        resp_ok = _openai_response(json.dumps({"ideas": [{"seed_index": 0, "title": "T", "angle": "A", "target_keyword": "k", "estimated_search_volume": "low"}]}))

        call_count = 0

        async def mock_send(request, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return resp_500
            return resp_ok

        with patch.object(llm._openai_client, "send", side_effect=mock_send):
            result = await llm.generate_ideas(
                seeds=[{"content": "test", "seed_type": "topic"}],
                ideas_per_seed=1,
                existing_titles=[],
            )

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_4xx_raises_immediately(self, llm):
        """4xx (non-429) should raise immediately, no retry."""
        resp_400 = httpx.Response(400, json={"error": {"message": "Bad request"}})

        async def mock_send(request, **kwargs):
            return resp_400

        with patch.object(llm._openai_client, "send", side_effect=mock_send):
            with pytest.raises(Exception):
                await llm.generate_ideas(
                    seeds=[{"content": "test", "seed_type": "topic"}],
                    ideas_per_seed=1,
                    existing_titles=[],
                )


# ─── Live tests (skipped without API keys) ───

@pytest.mark.skipif(
    not os.environ.get("TEST_LIVE_APIS"),
    reason="No live API keys — set TEST_LIVE_APIS=true to run",
)
class TestLiveLLMContracts:
    """These test real API calls. Only run with TEST_LIVE_APIS=true."""

    @pytest.fixture
    def live_config(self):
        return Config()

    @pytest.fixture
    def live_llm(self, live_config):
        return LiveLLM(live_config)

    @pytest.mark.asyncio
    async def test_live_ideation(self, live_llm):
        result = await live_llm.generate_ideas(
            seeds=[{"content": "sustainable packaging", "seed_type": "topic"}],
            ideas_per_seed=2,
            existing_titles=[],
        )
        assert len(result["ideas"]) == 2

    @pytest.mark.asyncio
    async def test_live_outline(self, live_llm):
        result = await live_llm.generate_outline(
            idea={"title": "Test", "target_keyword": "packaging", "angle": "Eco angle"},
            blog_context={"ghost_url": "https://blog.example.com"},
            target_word_count=1000,
        )
        assert "sections" in result
        assert len(result["sections"]) >= 4

    @pytest.mark.asyncio
    async def test_live_humanize(self, live_llm):
        result = await live_llm.humanize("# Test Article\n\nIn today's fast-paced world, we delve into...\n\n[IMAGE_ANCHOR:0]\n\n## Section\n\nContent here.")
        assert "[IMAGE_ANCHOR:0]" in result

    @pytest.mark.asyncio
    async def test_live_critique(self, live_llm):
        result = await live_llm.critique_draft(
            humanized_md="# Test\n\n## Intro\n\nSome content.\n\n## Body\n\nMore content.",
            outline={"sections": [{"heading": "Intro"}, {"heading": "Body"}]},
            seo_meta={"focus_keyword": "test"},
            iteration_number=1,
            max_iterations=3,
            article_title="Test Article",
            article_angle="Testing the critique system",
            search_intent="informational",
            focus_keyword="test",
        )
        assert "score" in result
        assert 1 <= result["score"] <= 10
