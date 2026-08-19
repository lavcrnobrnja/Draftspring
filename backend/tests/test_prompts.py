"""Tests for prompt templates (Task 3.1)."""

import pytest

from app.llm.prompts import (
    ideation_system_prompt,
    outline_system_prompt,
    drafting_system_prompt,
    humanizer_system_prompt,
    critique_system_prompt,
    image_prompter_system_prompt,
    image_prompter_user_message,
    alt_text_system_prompt,
)


class TestIdeationPrompt:
    def test_fills_ghost_url(self):
        prompt = ideation_system_prompt(ghost_url="https://blog.example.com", ideas_per_seed=3)
        assert "https://blog.example.com" in prompt
        assert "3" in prompt

    def test_fills_ideas_per_seed(self):
        prompt = ideation_system_prompt(ghost_url="https://x.com", ideas_per_seed=5)
        assert "5" in prompt

    def test_contains_json_instruction(self):
        prompt = ideation_system_prompt(ghost_url="https://x.com", ideas_per_seed=3)
        assert "JSON" in prompt

    def test_includes_brand_voice(self):
        prompt = ideation_system_prompt(
            ghost_url="https://x.com", ideas_per_seed=3, brand_voice="Casual and witty"
        )
        assert "Brand voice: Casual and witty" in prompt

    def test_includes_existing_titles(self):
        prompt = ideation_system_prompt(
            ghost_url="https://x.com", ideas_per_seed=3,
            existing_titles=["Title One", "Title Two"],
        )
        assert "- Title One" in prompt
        assert "- Title Two" in prompt
        assert "avoid duplicating" in prompt.lower()

    def test_regen_includes_feedback(self):
        prompt = ideation_system_prompt(
            ghost_url="https://x.com", ideas_per_seed=3,
            feedback="More technical angles please",
            rejected_titles=["Old Title 1", "Old Title 2"],
        )
        assert "REGENERATION CONTEXT" in prompt
        assert "More technical angles please" in prompt
        assert '"Old Title 1"' in prompt
        assert '"Old Title 2"' in prompt

    def test_no_regen_without_feedback(self):
        prompt = ideation_system_prompt(ghost_url="https://x.com", ideas_per_seed=3)
        assert "REGENERATION CONTEXT" not in prompt

    def test_includes_search_intent_in_schema(self):
        prompt = ideation_system_prompt(ghost_url="https://x.com", ideas_per_seed=3)
        assert "search_intent" in prompt


class TestOutlinePrompt:
    def test_fills_word_count(self):
        prompt = outline_system_prompt(target_word_count=2000, brand_voice="Casual and fun")
        assert "2000" in prompt

    def test_fills_brand_voice(self):
        prompt = outline_system_prompt(target_word_count=1500, brand_voice="Professional but conversational")
        assert "Professional but conversational" in prompt

    def test_contains_json_instruction(self):
        prompt = outline_system_prompt(target_word_count=1500, brand_voice="pro")
        assert "JSON" in prompt

    def test_contains_editorial_planner_role(self):
        prompt = outline_system_prompt(target_word_count=1500, brand_voice="pro")
        assert "senior editorial planner" in prompt

    def test_contains_search_intent_reference(self):
        prompt = outline_system_prompt(target_word_count=1500, brand_voice="pro")
        assert "search_intent" in prompt

    def test_contains_purpose_in_schema(self):
        prompt = outline_system_prompt(target_word_count=1500, brand_voice="pro")
        assert "purpose" in prompt

    def test_contains_word_count_target_in_schema(self):
        prompt = outline_system_prompt(target_word_count=1500, brand_voice="pro")
        assert "word_count_target" in prompt

    def test_contains_thesis_in_schema(self):
        prompt = outline_system_prompt(target_word_count=1500, brand_voice="pro")
        assert "thesis" in prompt


class TestDraftingPrompt:
    def test_basic_prompt(self):
        prompt = drafting_system_prompt()
        assert "IMAGE_ANCHOR" in prompt
        assert "Markdown" in prompt

    def test_with_previous_critique(self):
        critique = '{"issues": [{"description": "weak intro"}]}'
        prompt = drafting_system_prompt(previous_critique_json=critique)
        assert "weak intro" in prompt

    def test_with_user_revision_notes(self):
        prompt = drafting_system_prompt(user_revision_notes="Make it more technical")
        assert "Make it more technical" in prompt

    def test_without_optional_fields(self):
        prompt = drafting_system_prompt()
        # Should not contain the conditional blocks
        assert "critique" not in prompt.lower() or "previous" not in prompt.lower()


class TestHumanizerPrompt:
    def test_contains_key_instructions(self):
        prompt = humanizer_system_prompt()
        assert "IMAGE_ANCHOR" in prompt
        assert "human" in prompt.lower()


class TestCritiquePrompt:
    def test_fills_iteration_info(self):
        prompt = critique_system_prompt(
            iteration_number=2, max_iterations=5,
            article_title="Test Title", article_angle="Test angle",
            search_intent="informational", focus_keyword="test keyword",
        )
        assert "2" in prompt
        assert "5" in prompt
        assert "Test Title" in prompt
        assert "Test angle" in prompt
        assert "informational" in prompt
        assert "test keyword" in prompt

    def test_contains_json_instruction(self):
        prompt = critique_system_prompt(
            iteration_number=1, max_iterations=3,
            article_title="Test", article_angle="Angle",
            search_intent="informational", focus_keyword="kw",
        )
        assert "JSON" in prompt

    def test_brand_voice_block(self):
        prompt = critique_system_prompt(
            iteration_number=1, max_iterations=3,
            article_title="Test", article_angle="Angle",
            search_intent="informational", focus_keyword="kw",
            brand_voice="Casual and witty",
        )
        assert "Brand voice: Casual and witty" in prompt

    def test_previous_issues_block(self):
        prompt = critique_system_prompt(
            iteration_number=2, max_iterations=3,
            article_title="Test", article_angle="Angle",
            search_intent="informational", focus_keyword="kw",
            previous_score=5, previous_issues_json='[{"severity": "major"}]',
        )
        assert "PREVIOUS CRITIQUE" in prompt
        assert "5/10" in prompt

    def test_no_previous_issues_on_iteration_1(self):
        prompt = critique_system_prompt(
            iteration_number=1, max_iterations=3,
            article_title="Test", article_angle="Angle",
            search_intent="informational", focus_keyword="kw",
        )
        assert "PREVIOUS CRITIQUE" not in prompt


class TestImagePrompterPrompt:
    def test_contains_key_instructions(self):
        prompt = image_prompter_system_prompt()
        assert "IMAGE_ANCHOR:COVER" in prompt
        assert "art director" in prompt.lower()
        assert "JSON" in prompt.upper() or "json" in prompt.lower()
        assert "no text" in prompt.lower()
        assert "route" in prompt.lower()
        assert "art_direction" in prompt
        assert "must stay blank or abstract" in prompt.lower()

    def test_user_message_template(self):
        msg = image_prompter_user_message("My Title", "my keyword", "Article body text here")
        assert "My Title" in msg
        assert "my keyword" in msg
        assert "Article body text here" in msg
        assert "IMAGE_ANCHOR" in msg


class TestAltTextPrompt:
    def test_basic_prompt(self):
        prompt = alt_text_system_prompt()
        assert "alt" in prompt.lower()
        assert "visually depicted" in prompt
        assert "10-20 words" in prompt
