"""Abstract LLM provider interface."""

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Abstract interface for LLM providers (Mock for Phase 2, Live for Phase 3)."""

    @abstractmethod
    async def generate_ideas(
        self, seeds: list[dict], ideas_per_seed: int, existing_titles: list[str],
        feedback: str | None = None, rejected_titles: list[str] | None = None,
        ghost_url: str = "blog", brand_voice: str | None = None,
        user_images: list[dict] | None = None,
    ) -> dict:
        """Generate article ideas from seed topics/URLs.
        
        feedback: user feedback for regeneration (e.g. "more technical angles")
        rejected_titles: titles from previous generations to avoid
        ghost_url: the user's blog URL
        brand_voice: optional brand voice description
        
        Returns: { ideas: [{ seed_index, title, angle, target_keyword, search_intent }], _mock }
        """
        ...

    @abstractmethod
    async def generate_outline(
        self,
        idea: dict,
        blog_context: dict,
        target_word_count: int,
        previous_feedback: str | None = None,
        content_brief: dict | None = None,
    ) -> dict:
        """Generate article outline with SEO block.
        
        Returns: { working_title, thesis, target_word_count, sections: [...], seo: {...}, _mock }
        """
        ...

    @abstractmethod
    async def draft_article(
        self,
        outline: dict,
        seo_meta: dict,
        brand_voice: str | None = None,
        focus_keyword: str = "",
        article_title: str = "",
        previous_critique: dict | None = None,
        previous_score: int | None = None,
        user_revision_notes: str | None = None,
        target_word_count: int = 1500,
        iteration_number: int = 1,
        content_brief: dict | None = None,
    ) -> str:
        """Write a complete article draft as Markdown."""
        ...

    @abstractmethod
    async def humanize(self, draft_md: str, brand_voice: str = "", focus_keyword: str = "", article_title: str = "") -> str:
        """Humanize an article draft, preserving IMAGE_ANCHOR tags and structure."""
        ...

    @abstractmethod
    async def critique_draft(
        self,
        humanized_md: str,
        outline: dict,
        seo_meta: dict,
        iteration_number: int,
        max_iterations: int,
        previous_critique: dict | None = None,
        article_title: str = "",
        article_angle: str = "",
        search_intent: str = "",
        focus_keyword: str = "",
        brand_voice: str | None = None,
        target_word_count: int | None = None,
        meta_description: str | None = None,
        user_description: str | None = None,
        user_keywords: str | None = None,
    ) -> dict:
        """Critique a draft. Returns verdict, score, issues, seo_check."""
        ...

    @abstractmethod
    async def generate_alt_texts(
        self, focus_keyword: str, images: list[dict]
    ) -> dict:
        """Generate alt texts for article images.
        
        Returns: { alt_texts: [str], _mock }
        """
        ...

    @abstractmethod
    async def generate_image_prompts(
        self, article_title: str, focus_keyword: str, article_text: str,
        image_slots: list[dict] | None = None,
        user_photo_descriptions: list[dict] | None = None,
        user_revision_notes: str | None = None,
        image_style_direction: str | None = None,
    ) -> dict:
        """Analyze a finished article and generate per-anchor image prompts.

        Returns: { route, route_rationale, art_direction: {direction, palette, cohesion, variation}, images: [{anchor, semantic_target, primary_subject, concrete_objects, composition_type, why_this_matches, prompt}] }
        """
        ...

    @abstractmethod
    async def describe_image(self, image_bytes: bytes) -> str:
        """Describe an image using vision model. Returns 2-3 sentence description.
        
        Used for content brief photo pre-processing before ideation.
        """
        ...

    @abstractmethod
    async def generate_image(self, prompt: str) -> bytes:
        """Generate an image from a prompt. Returns raw image bytes."""
        ...
