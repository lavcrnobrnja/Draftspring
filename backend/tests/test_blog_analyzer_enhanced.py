"""Tests for enhanced BlogAnalyzer features — new fields, backward compat."""

import json
import pytest
from dataclasses import asdict
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.blog_analyzer import BlogProfile, BlogAnalyzer, ArticleIdea


class TestBlogProfileEnhancedFields:
    """Test that BlogProfile supports new fields with backward compatibility."""

    def test_new_fields_have_defaults(self):
        """New fields default to empty — backward compat for existing profiles."""
        profile = BlogProfile(
            id="p1",
            url="https://example.com",
            site_name="Test",
            is_ghost=True,
        )
        assert profile.audience_description == ""
        assert profile.tone_keywords == []
        assert profile.strengths == []

    def test_new_fields_set(self):
        """New fields can be set."""
        profile = BlogProfile(
            id="p1",
            url="https://example.com",
            site_name="Test",
            is_ghost=True,
            audience_description="Python developers",
            tone_keywords=["conversational", "technical"],
            strengths=["clear examples"],
        )
        assert profile.audience_description == "Python developers"
        assert profile.tone_keywords == ["conversational", "technical"]
        assert profile.strengths == ["clear examples"]

    def test_asdict_includes_new_fields(self):
        """asdict() includes the new fields for serialization."""
        profile = BlogProfile(
            id="p1",
            url="https://example.com",
            site_name="Test",
            is_ghost=True,
            audience_description="devs",
            tone_keywords=["casual"],
            strengths=["practical"],
        )
        d = asdict(profile)
        assert d["audience_description"] == "devs"
        assert d["tone_keywords"] == ["casual"]
        assert d["strengths"] == ["practical"]

    def test_profile_from_old_data(self):
        """Profiles cached without new fields should still load fine."""
        # Simulate old cache data (no new fields)
        old_profile_data = {
            "topics": ["python"],
            "content_gaps": ["testing"],
            "style_guide": "Direct.",
            "example_sentences": ["Boom."],
            "avg_word_count": 1000,
            "total_posts": 10,
            "latest_post_date": "",
            "publishing_frequency": "weekly",
            "post_summaries": [],
            # No audience_description, tone_keywords, strengths
        }
        profile = BlogProfile(
            id="p1",
            url="https://example.com",
            site_name="Test",
            is_ghost=True,
            topics=old_profile_data.get("topics", []),
            content_gaps=old_profile_data.get("content_gaps", []),
            style_guide=old_profile_data.get("style_guide", ""),
            example_sentences=old_profile_data.get("example_sentences", []),
            audience_description=old_profile_data.get("audience_description", ""),
            tone_keywords=old_profile_data.get("tone_keywords", []),
            strengths=old_profile_data.get("strengths", []),
            avg_word_count=old_profile_data.get("avg_word_count", 0),
        )
        # Should not raise, defaults apply
        assert profile.audience_description == ""
        assert profile.tone_keywords == []
        assert profile.strengths == []


class TestEnhancedPostProcessing:
    """Test that post limits and truncation are updated."""

    def test_format_posts_uses_2500_chars(self):
        """Post content should be truncated to 2500 chars, not 1500."""
        from app.config import get_config
        config = get_config()
        analyzer = BlogAnalyzer(config)

        # Create a post with 3000 chars of content
        long_content = "<p>" + "a" * 3000 + "</p>"
        posts = [{"title": "Long Post", "date": "", "tags": [], "content": long_content}]

        formatted = analyzer._format_posts_for_llm(posts)
        # The text content should be roughly 2500 chars (stripped HTML)
        # We check that it's longer than 1500 (old limit) but not 3000
        lines = formatted.split("\n")
        content_text = ""
        for line in lines:
            if line.startswith("### ") or line.startswith("Date:") or line.startswith("Tags:") or line.startswith("Word count:") or line.startswith("---"):
                continue
            content_text += line
        assert len(content_text) >= 2000  # Should be ~2500, definitely > old 1500
        assert len(content_text) <= 2600  # Should be capped at ~2500


class TestEnhancedIdeaGeneration:
    """Test that idea generation includes existing titles and audience info."""

    @pytest.mark.asyncio
    async def test_generate_ideas_includes_existing_titles(self):
        """Prompt should include existing post titles to avoid duplicates."""
        from app.config import get_config
        config = get_config()
        analyzer = BlogAnalyzer(config)

        profile = BlogProfile(
            id="p1",
            url="https://example.com",
            site_name="Test",
            is_ghost=True,
            topics=["python"],
            content_gaps=["testing"],
            style_guide="Direct.",
            post_summaries=[
                {"title": "Existing Post 1", "url": "/p1", "date": ""},
                {"title": "Existing Post 2", "url": "/p2", "date": ""},
            ],
            audience_description="Python developers",
        )

        captured_prompt = None

        async def mock_gemini_call(system_prompt, user_content, temperature=0.3, max_tokens=8000):
            nonlocal captured_prompt
            captured_prompt = user_content
            return json.dumps([{
                "title": "New Idea",
                "angle": "Fresh take",
                "article_type": "how-to",
                "reasoning": "Fills gap",
            }]), {}

        analyzer._gemini_call = mock_gemini_call

        ideas = await analyzer.generate_ideas(profile, count=1)

        # Verify the prompt includes existing titles
        assert "Existing Post 1" in captured_prompt
        assert "Existing Post 2" in captured_prompt
        assert "DO NOT duplicate" in captured_prompt
        assert "Python developers" in captured_prompt

    @pytest.mark.asyncio
    async def test_generate_ideas_works_without_audience(self):
        """Should work fine when audience_description is empty (backward compat)."""
        from app.config import get_config
        config = get_config()
        analyzer = BlogAnalyzer(config)

        profile = BlogProfile(
            id="p1",
            url="https://example.com",
            site_name="Test",
            is_ghost=True,
            topics=["python"],
            content_gaps=["testing"],
            style_guide="Direct.",
            post_summaries=[],
            audience_description="",  # empty = old profile
        )

        async def mock_gemini_call(system_prompt, user_content, temperature=0.3, max_tokens=8000):
            return json.dumps([{
                "title": "Idea",
                "angle": "Angle",
                "article_type": "how-to",
                "reasoning": "Reason",
            }]), {}

        analyzer._gemini_call = mock_gemini_call

        ideas = await analyzer.generate_ideas(profile, count=1)
        assert len(ideas) == 1
        assert ideas[0].title == "Idea"


class TestEnhancedProfileExtraction:
    """Test that the extraction prompt asks for new fields."""

    @pytest.mark.asyncio
    async def test_extraction_prompt_requests_new_fields(self):
        """Gemini extraction prompt should request audience_description, tone_keywords, strengths."""
        from app.config import get_config
        config = get_config()
        analyzer = BlogAnalyzer(config)

        captured_system = None

        async def mock_gemini_call(system_prompt, user_content, temperature=0.3, max_tokens=8000):
            nonlocal captured_system
            captured_system = system_prompt
            return json.dumps({
                "topics": ["python"],
                "content_gaps": ["testing"],
                "style_guide": "Direct.",
                "example_sentences": ["Hi."],
                "audience_description": "developers",
                "tone_keywords": ["casual"],
                "strengths": ["examples"],
                "avg_word_count": 1000,
                "publishing_frequency": "weekly",
            }), {}

        analyzer._gemini_call = mock_gemini_call

        result = await analyzer._extract_profile_via_gemini(
            site_name="Test",
            url="https://example.com",
            posts_text="Some posts...",
            post_count=10,
        )

        assert "audience_description" in captured_system
        assert "tone_keywords" in captured_system
        assert "strengths" in captured_system
        assert result["audience_description"] == "developers"
        assert result["tone_keywords"] == ["casual"]
        assert result["strengths"] == ["examples"]
