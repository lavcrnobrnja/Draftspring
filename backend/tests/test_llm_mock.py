"""Tests for LLM interface and MockLLM implementation (Task 2.1)."""

import json
import re

import pytest
import pytest_asyncio

from app.llm.base import LLMProvider
from app.llm.mock import MockLLM


@pytest.fixture
def llm():
    return MockLLM()


class TestLLMProviderInterface:
    """Verify MockLLM implements the abstract interface."""

    def test_is_llm_provider(self, llm):
        assert isinstance(llm, LLMProvider)

    def test_has_all_methods(self, llm):
        for method in [
            "generate_ideas",
            "generate_outline",
            "draft_article",
            "humanize",
            "critique_draft",
            "generate_alt_texts",
            "generate_image",
        ]:
            assert callable(getattr(llm, method))


class TestGenerateIdeas:
    @pytest.mark.asyncio
    async def test_returns_correct_count(self, llm):
        seeds = [
            {"seed_type": "topic", "content": "AI in healthcare"},
            {"seed_type": "url", "content": "https://example.com/article"},
        ]
        result = await llm.generate_ideas(seeds, ideas_per_seed=3, existing_titles=[])
        assert result["_mock"] is True
        assert len(result["ideas"]) == 6  # 2 seeds × 3

    @pytest.mark.asyncio
    async def test_idea_structure(self, llm):
        seeds = [{"seed_type": "topic", "content": "Python testing"}]
        result = await llm.generate_ideas(seeds, ideas_per_seed=2, existing_titles=[])
        idea = result["ideas"][0]
        assert "seed_index" in idea
        assert "title" in idea and len(idea["title"]) > 0
        assert "angle" in idea and len(idea["angle"]) > 0
        assert "target_keyword" in idea and len(idea["target_keyword"]) > 0
        assert idea["estimated_search_volume"] in ("low", "medium", "high")

    @pytest.mark.asyncio
    async def test_seed_index_mapping(self, llm):
        seeds = [
            {"seed_type": "topic", "content": "Topic A"},
            {"seed_type": "topic", "content": "Topic B"},
        ]
        result = await llm.generate_ideas(seeds, ideas_per_seed=2, existing_titles=[])
        indices = [i["seed_index"] for i in result["ideas"]]
        assert indices.count(0) == 2
        assert indices.count(1) == 2

    @pytest.mark.asyncio
    async def test_no_duplicate_keywords(self, llm):
        seeds = [{"seed_type": "topic", "content": "Testing"}]
        result = await llm.generate_ideas(seeds, ideas_per_seed=3, existing_titles=[])
        keywords = [i["target_keyword"] for i in result["ideas"]]
        assert len(keywords) == len(set(keywords))

    @pytest.mark.asyncio
    async def test_single_seed_single_idea(self, llm):
        seeds = [{"seed_type": "topic", "content": "Minimal"}]
        result = await llm.generate_ideas(seeds, ideas_per_seed=1, existing_titles=[])
        assert len(result["ideas"]) == 1


class TestGenerateOutline:
    @pytest.mark.asyncio
    async def test_outline_structure(self, llm):
        idea = {"title": "Test Article", "target_keyword": "test keyword"}
        result = await llm.generate_outline(idea, blog_context={}, target_word_count=1500)
        assert result["_mock"] is True
        assert "working_title" in result
        assert "thesis" in result
        assert "sections" in result
        assert "seo_block" in result

    @pytest.mark.asyncio
    async def test_five_sections(self, llm):
        idea = {"title": "Test", "target_keyword": "test"}
        result = await llm.generate_outline(idea, blog_context={}, target_word_count=1500)
        assert len(result["sections"]) == 5

    @pytest.mark.asyncio
    async def test_image_sections(self, llm):
        """Sections 1, 3, 5 (0-indexed: 0, 2, 4) should have images."""
        idea = {"title": "Test", "target_keyword": "test"}
        result = await llm.generate_outline(idea, blog_context={}, target_word_count=1500)
        sections = result["sections"]
        assert sections[0]["image_needed"] is True
        assert sections[1]["image_needed"] is False
        assert sections[2]["image_needed"] is True
        assert sections[3]["image_needed"] is False
        assert sections[4]["image_needed"] is True

    @pytest.mark.asyncio
    async def test_image_needed_flag_present(self, llm):
        idea = {"title": "Test", "target_keyword": "test"}
        result = await llm.generate_outline(idea, blog_context={}, target_word_count=1500)
        for s in result["sections"]:
            assert "image_needed" in s
            assert isinstance(s["image_needed"], bool)

    @pytest.mark.asyncio
    async def test_seo_block_valid(self, llm):
        idea = {"title": "Test", "target_keyword": "test keyword"}
        result = await llm.generate_outline(idea, blog_context={}, target_word_count=1500)
        seo = result["seo_block"]
        assert len(seo["focus_keyword"]) > 0
        assert len(seo["meta_title"]) <= 60
        assert len(seo["meta_description"]) <= 155
        assert isinstance(seo["visible_tags"], list)
        assert len(seo["visible_tags"]) >= 1

    @pytest.mark.asyncio
    async def test_section_structure(self, llm):
        idea = {"title": "Test", "target_keyword": "test"}
        result = await llm.generate_outline(idea, blog_context={}, target_word_count=1500)
        section = result["sections"][0]
        assert "subheading" in section
        assert "section_number" in section
        assert "purpose" in section
        assert "key_points" in section
        assert "research_notes" in section
        assert isinstance(section["research_notes"], list)
        assert "word_count_target" in section


class TestDraftArticle:
    @pytest.mark.asyncio
    async def test_returns_markdown(self, llm):
        outline = {
            "working_title": "Test Article",
            "sections": [
                {"heading": f"Section {i}", "image_needed": i % 2 == 0, "image_guidance": "desc"}
                for i in range(5)
            ],
        }
        seo = {"focus_keyword": "test keyword"}
        result = await llm.draft_article(outline, seo, brand_voice="professional")
        assert isinstance(result, str)
        assert "# " in result  # Has H1

    @pytest.mark.asyncio
    async def test_has_h2s(self, llm):
        outline = {
            "working_title": "Test Article",
            "sections": [
                {"heading": f"Section {i}", "image_needed": i % 2 == 0, "image_guidance": "desc"}
                for i in range(5)
            ],
        }
        seo = {"focus_keyword": "test keyword"}
        result = await llm.draft_article(outline, seo, brand_voice="professional")
        h2_count = len(re.findall(r"^## ", result, re.MULTILINE))
        assert h2_count >= 5

    @pytest.mark.asyncio
    async def test_has_image_anchors(self, llm):
        outline = {
            "working_title": "Test Article",
            "sections": [
                {"heading": f"Section {i}", "image_needed": i % 2 == 0, "image_guidance": "desc"}
                for i in range(5)
            ],
        }
        seo = {"focus_keyword": "test keyword"}
        result = await llm.draft_article(outline, seo, brand_voice="professional")
        anchors = re.findall(r"\[IMAGE_ANCHOR:(?:COVER|\d+)\]", result)
        assert len(anchors) >= 2  # At least some image sections
        # First anchor should be COVER
        assert "[IMAGE_ANCHOR:COVER]" in result

    @pytest.mark.asyncio
    async def test_word_count_approximately_500(self, llm):
        outline = {
            "working_title": "Test Article",
            "sections": [
                {"heading": f"Section {i}", "image_needed": i % 2 == 0, "image_guidance": "desc"}
                for i in range(5)
            ],
        }
        seo = {"focus_keyword": "test keyword"}
        result = await llm.draft_article(outline, seo, brand_voice="professional")
        word_count = len(result.split())
        assert 300 <= word_count <= 800  # ~500 words, some tolerance

    @pytest.mark.asyncio
    async def test_keyword_placement(self, llm):
        outline = {
            "working_title": "Test Article",
            "sections": [
                {"heading": f"Section {i}", "image_needed": i % 2 == 0, "image_guidance": "desc"}
                for i in range(5)
            ],
        }
        seo = {"focus_keyword": "test keyword"}
        result = await llm.draft_article(outline, seo, brand_voice="professional")
        assert result.lower().count("test keyword") >= 3


class TestHumanize:
    @pytest.mark.asyncio
    async def test_returns_modified_text(self, llm):
        draft = "# Title\n\nSome text here.\n\n[IMAGE_ANCHOR:COVER]\n\n## Section\n\nMore text."
        result = await llm.humanize(draft)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_preserves_image_anchors(self, llm):
        draft = "# Title\n\nText.\n\n[IMAGE_ANCHOR:COVER]\n\n## Section\n\n[IMAGE_ANCHOR:1]\n\nMore."
        result = await llm.humanize(draft)
        assert "[IMAGE_ANCHOR:COVER]" in result
        assert "[IMAGE_ANCHOR:1]" in result

    @pytest.mark.asyncio
    async def test_preserves_headings(self, llm):
        draft = "# Main Title\n\n## Sub One\n\n## Sub Two\n\nContent."
        result = await llm.humanize(draft)
        assert "# " in result
        assert "## " in result


class TestCritiqueDraft:
    CRITIQUE_KWARGS = dict(
        article_title="Test Title",
        article_angle="Test angle",
        search_intent="informational",
        focus_keyword="test keyword",
    )

    @pytest.mark.asyncio
    async def test_iteration_1_revision_needed(self, llm):
        result = await llm.critique_draft(
            humanized_md="Some draft text",
            outline={},
            seo_meta={},
            iteration_number=1,
            max_iterations=3,
            **self.CRITIQUE_KWARGS,
        )
        assert result["_mock"] is True
        assert result["verdict"] == "revision_needed"
        assert result["score"] == 6
        assert len(result["issues"]) > 0
        for issue in result["issues"]:
            assert len(issue["fix"]) > 20

    @pytest.mark.asyncio
    async def test_iteration_2_approved(self, llm):
        result = await llm.critique_draft(
            humanized_md="Some draft text",
            outline={},
            seo_meta={},
            iteration_number=2,
            max_iterations=3,
            **self.CRITIQUE_KWARGS,
        )
        assert result["verdict"] == "approved"
        assert result["score"] == 8

    @pytest.mark.asyncio
    async def test_iteration_3_score_7_revision_needed(self, llm):
        """Iteration 3: score 7 but verdict revision_needed — tests software override."""
        result = await llm.critique_draft(
            humanized_md="Some draft text",
            outline={},
            seo_meta={},
            iteration_number=3,
            max_iterations=3,
            **self.CRITIQUE_KWARGS,
        )
        assert result["verdict"] == "revision_needed"
        assert result["score"] == 7
        # Software override: score >= 7 means approved regardless

    @pytest.mark.asyncio
    async def test_critique_structure(self, llm):
        result = await llm.critique_draft(
            humanized_md="text",
            outline={},
            seo_meta={},
            iteration_number=1,
            max_iterations=3,
            **self.CRITIQUE_KWARGS,
        )
        assert "issues" in result and isinstance(result["issues"], list)
        assert "seo_check" in result
        assert "summary" in result and isinstance(result["summary"], str)
        assert 1 <= result["score"] <= 10

    @pytest.mark.asyncio
    async def test_seo_check_structure(self, llm):
        result = await llm.critique_draft(
            humanized_md="text",
            outline={},
            seo_meta={},
            iteration_number=1,
            max_iterations=3,
            **self.CRITIQUE_KWARGS,
        )
        seo = result["seo_check"]
        assert "meta_fix_suggestion" in seo


class TestGenerateAltTexts:
    @pytest.mark.asyncio
    async def test_generates_for_each_image(self, llm):
        images = [
            {"section_heading": "Introduction", "image_guidance": "A desk setup"},
            {"section_heading": "Analysis", "image_guidance": "Chart showing trends"},
        ]
        result = await llm.generate_alt_texts("test keyword", images)
        assert result["_mock"] is True
        assert len(result["alt_texts"]) == 2

    @pytest.mark.asyncio
    async def test_alt_text_contains_heading(self, llm):
        images = [
            {"section_heading": "The Future of AI", "image_guidance": "Robot hand"},
        ]
        result = await llm.generate_alt_texts("ai future", images)
        assert "The Future of AI" in result["alt_texts"][0]


class TestGenerateImagePrompts:
    @pytest.mark.asyncio
    async def test_returns_prompts_for_all_anchors(self, llm):
        article = (
            "# Test Article\n\nIntro text.\n\n[IMAGE_ANCHOR:COVER]\n\n"
            "## Section 1\n\nContent.\n\n[IMAGE_ANCHOR:1]\n\n"
            "## Section 2\n\nMore content.\n\n[IMAGE_ANCHOR:2]\n\n"
            "## Conclusion\n\nWrapping up."
        )
        result = await llm.generate_image_prompts("Test Article", "test keyword", article)
        assert result["_mock"] is True
        assert len(result["images"]) == 3
        anchors = [img["anchor"] for img in result["images"]]
        assert "IMAGE_ANCHOR:COVER" in anchors
        assert "IMAGE_ANCHOR:1" in anchors
        assert "IMAGE_ANCHOR:2" in anchors

    @pytest.mark.asyncio
    async def test_cover_anchor_present(self, llm):
        article = "# Title\n\n[IMAGE_ANCHOR:COVER]\n\n## Section\n\n[IMAGE_ANCHOR:1]"
        result = await llm.generate_image_prompts("Title", "keyword", article)
        cover = [img for img in result["images"] if img["anchor"] == "IMAGE_ANCHOR:COVER"]
        assert len(cover) == 1

    @pytest.mark.asyncio
    async def test_inline_anchors_present(self, llm):
        article = "# Title\n\n[IMAGE_ANCHOR:COVER]\n\n## Section\n\n[IMAGE_ANCHOR:1]"
        result = await llm.generate_image_prompts("Title", "keyword", article)
        inlines = [img for img in result["images"] if img["anchor"] != "IMAGE_ANCHOR:COVER"]
        assert len(inlines) == 1
        assert inlines[0]["anchor"] == "IMAGE_ANCHOR:1"

    @pytest.mark.asyncio
    async def test_prompts_contain_no_text_instruction(self, llm):
        article = "# Title\n\n[IMAGE_ANCHOR:COVER]\n\nContent."
        result = await llm.generate_image_prompts("Title", "keyword", article)
        for img in result["images"]:
            assert "no text" in img["prompt"].lower()
            assert "no watermarks" in img["prompt"].lower()

    @pytest.mark.asyncio
    async def test_art_direction_included(self, llm):
        article = "# Title\n\n[IMAGE_ANCHOR:COVER]\n\nContent."
        result = await llm.generate_image_prompts("Title", "keyword", article)
        assert "route" in result
        assert "route_rationale" in result
        assert "art_direction" in result
        assert "palette" in result["art_direction"]


class TestGenerateImage:
    @pytest.mark.asyncio
    async def test_returns_bytes(self, llm):
        result = await llm.generate_image("A beautiful sunset over mountains")
        assert isinstance(result, bytes)
        assert len(result) == 100
