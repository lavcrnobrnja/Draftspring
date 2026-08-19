"""Mock LLM provider for Phase 2 testing. Deterministic responses."""

from app.llm.base import LLMProvider


class MockLLM(LLMProvider):
    """Deterministic mock LLM for testing the state machine without real API calls."""

    async def describe_image(self, image_bytes: bytes) -> str:
        """Return mock image description."""
        return "A professional photograph showing a modern workspace with warm natural lighting. The scene features a clean desk setup with a laptop and notebook, conveying a productive and focused atmosphere. The color palette is dominated by warm wood tones and soft whites."

    async def generate_ideas(
        self, seeds: list[dict], ideas_per_seed: int, existing_titles: list[str],
        feedback: str | None = None, rejected_titles: list[str] | None = None,
        ghost_url: str = "blog", brand_voice: str | None = None,
        user_images: list[dict] | None = None,
    ) -> dict:
        ideas = []
        keyword_counter = 0
        regen_prefix = "(Regen) " if feedback else ""
        for seed_idx, seed in enumerate(seeds):
            content = seed.get("content", "topic")
            for j in range(ideas_per_seed):
                keyword_counter += 1
                title = f"{regen_prefix}Article about {content} — angle {j + 1}"
                # Skip rejected titles
                if rejected_titles and title in rejected_titles:
                    title = f"{regen_prefix}Fresh take on {content} — angle {j + 1}"
                ideas.append({
                    "seed_index": seed_idx,
                    "title": title,
                    "angle": f"Exploring {content} from perspective {j + 1}, covering key insights and practical applications.",
                    "target_keyword": f"{content.lower().replace(' ', '-')}-{keyword_counter}",
                    "estimated_search_volume": ["low", "medium", "high"][j % 3],
                    "search_intent": f"The reader wants to learn about {content} from perspective {j + 1}.",
                })
        return {"ideas": ideas, "_mock": True}

    async def generate_outline(
        self,
        idea: dict,
        blog_context: dict,
        target_word_count: int,
        previous_feedback: str | None = None,
        content_brief: dict | None = None,
    ) -> dict:
        keyword = idea.get("target_keyword", "mock keyword")
        title = idea.get("title", "Mock Article")
        num_sections = 5
        per_section = target_word_count // num_sections
        remainder = target_word_count - per_section * num_sections
        sections = []
        for i in range(num_sections):
            has_image = i in (0, 2, 4)  # sections 1, 3, 5 (0-indexed: 0, 2, 4)
            wc = per_section + (1 if i < remainder else 0)
            sections.append({
                "section_number": i + 1,
                "subheading": f"Section {i + 1}: {title} Part {i + 1}",
                "purpose": f"This section covers part {i + 1} of the argument about {keyword}.",
                "key_points": [f"Key point about {keyword} number {j + 1}" for j in range(3)],
                "research_notes": [f"Research note {j + 1} for section {i + 1} covering {keyword}." for j in range(2)],
                "word_count_target": wc,
                "image_needed": has_image,
            })
        return {
            "working_title": title,
            "thesis": f"This article explores {keyword} in depth, providing actionable insights.",
            "target_word_count": target_word_count,
            "sections": sections,
            "seo_block": {
                "focus_keyword": keyword,
                "meta_title": f"{title}"[:60],
                "meta_description": f"Learn about {keyword}. This guide covers everything you need to know."[:155],
                "visible_tags": ["mock topic", "testing", "guide"],
            },
            "_mock": True,
        }

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
        keyword = focus_keyword or seo_meta.get("focus_keyword", "test keyword")
        title = article_title or outline.get("working_title", "Mock Article")
        sections = outline.get("sections", [])

        lines = [f"# {title}: A Complete Guide to {keyword}\n"]
        lines.append(f"In this article, we explore {keyword} and why it matters. "
                      f"Understanding {keyword} is essential for anyone looking to stay ahead.\n")

        body_anchor_idx = 1
        first_image = True
        for i, section in enumerate(sections):
            heading = section.get("subheading", section.get("heading", f"Section {i + 1}"))
            lines.append(f"## {heading}\n")

            # Paragraph with ~80 words per section
            lines.append(
                f"When it comes to {keyword}, this section covers important ground. "
                f"The key aspects of {heading.lower()} are multifaceted and deserve careful attention. "
                f"Research shows that understanding these fundamentals leads to better outcomes. "
                f"Experts in the field consistently emphasize the importance of this topic. "
                f"By examining the evidence carefully, we can draw meaningful conclusions. "
                f"Let's dive deeper into what makes this area particularly interesting and relevant. "
                f"The practical implications extend far beyond what most people initially realize. "
                f"In the following paragraphs, we break down each component systematically.\n"
            )

            if section.get("image_needed"):
                if first_image:
                    lines.append("[IMAGE_ANCHOR:COVER]\n")
                    first_image = False
                else:
                    lines.append(f"[IMAGE_ANCHOR:{body_anchor_idx}]\n")
                    body_anchor_idx += 1

        lines.append(f"## Conclusion\n")
        lines.append(
            f"In summary, {keyword} represents a critical area worth understanding deeply. "
            f"We've covered the main aspects of {keyword} throughout this article. "
            f"Take action today to apply these insights in your own work and practice.\n"
        )

        return "\n".join(lines)

    async def humanize(self, draft_md: str, brand_voice: str = "", focus_keyword: str = "", article_title: str = "") -> str:
        # Minor modifications while preserving structure and anchors
        result = draft_md
        # Add some "human" touches
        result = result.replace("In this article, we explore", "Look, let's talk about")
        result = result.replace("In summary,", "Bottom line:")
        result = result.replace("Research shows that", "Here's what the research says:")
        return result

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
        base = {
            "seo_check": {
                "meta_fix_suggestion": None,
            },
            "_mock": True,
        }

        if iteration_number == 1:
            base["score"] = 6
            base["verdict"] = "revision_needed"
            base["summary"] = "The draft has structural issues and the introduction lacks a hook. The thesis is buried and needs to be moved to the opening."
            base["issues"] = [
                {
                    "severity": "major",
                    "location": "Introduction",
                    "description": "The introduction lacks a compelling hook to draw readers in.",
                    "fix": "Add a surprising statistic or provocative question in the first paragraph to immediately engage readers.",
                },
                {
                    "severity": "minor",
                    "location": "Section 3",
                    "description": "Transition between paragraphs is abrupt.",
                    "fix": "Add a transitional sentence connecting the previous point about fundamentals to the practical applications discussed next.",
                },
            ]
        elif iteration_number == 2:
            base["score"] = 8
            base["verdict"] = "approved"
            base["summary"] = "Strong improvement from the previous draft. The thesis is clear, the structure is solid, and the voice sounds natural."
            base["issues"] = []
        else:
            # Iteration 3+: score 7 but verdict says revision_needed
            # This tests the software override rule (score >= 7 → approved)
            base["score"] = 7
            base["verdict"] = "revision_needed"
            base["summary"] = "The article is solid overall with minor polish needed. The conclusion could land harder but nothing structural remains."
            base["issues"] = [
                {
                    "severity": "minor",
                    "location": "Conclusion",
                    "description": "Conclusion could be slightly more impactful.",
                    "fix": "Consider ending with a forward-looking statement about the future implications rather than a generic call to action.",
                },
            ]

        return base

    async def generate_image_prompts(
        self, article_title: str, focus_keyword: str, article_text: str,
        image_slots: list[dict] | None = None,
        user_photo_descriptions: list[dict] | None = None,
        user_revision_notes: str | None = None,
        image_style_direction: str | None = None,
    ) -> dict:
        import re
        if image_slots:
            anchors = [slot["anchor"].replace("IMAGE_ANCHOR:", "") for slot in image_slots]
            slots_by_anchor = {slot["anchor"]: slot for slot in image_slots}
        else:
            anchors = re.findall(r"\[IMAGE_ANCHOR:(COVER|\d+)\]", article_text)
            slots_by_anchor = {}
        images = []
        for anchor in anchors:
            anchor_key = f"IMAGE_ANCHOR:{anchor}"
            is_cover = anchor == "COVER"
            slot = slots_by_anchor.get(anchor_key, {})
            key_points = slot.get("key_points") or [focus_keyword]
            if isinstance(key_points, str):
                key_points = [key_points]
            heading = slot.get("heading") or ("Article cover" if is_cover else f"Section {anchor}")
            primary_subject = (
                "support workflow command center" if is_cover
                else f"{heading.lower()} visual system"
            )
            composition_type = "wide editorial overview" if is_cover else f"section-specific composition {anchor}"
            concrete_objects = [str(p) for p in key_points[:3]] or [focus_keyword]
            images.append({
                "anchor": anchor_key,
                "semantic_target": slot.get("semantic_target") or f"Mock semantic target for {anchor}",
                "primary_subject": primary_subject,
                "concrete_objects": concrete_objects,
                "composition_type": composition_type,
                "why_this_matches": f"Matches {heading} using {', '.join(concrete_objects)}.",
                "section_idea": f"Mock section idea for {anchor}",
                "emotional_beat": "establishing" if is_cover else "developing",
                "prompt": (
                    f"{image_style_direction + ' ' if image_style_direction else ''}"
                    f"Mock editorial photograph for {article_title}, anchor {anchor}: "
                    f"{primary_subject} with {', '.join(concrete_objects)}, "
                    f"Professional lighting, shallow depth of field, "
                    f"magazine-quality composition. "
                    f"16:9 aspect ratio, high resolution, no text, no watermarks."
                ),
            })
        return {
            "route": "2",
            "route_rationale": "Mock: photographing something real that evokes the subject",
            "art_direction": {
                "direction": "Mock art direction for testing",
                "palette": "warm earth tones, muted greens, charcoal",
                "cohesion": "natural light, editorial framing",
                "variation": "distance and angle change between images",
            },
            "images": images,
            "_mock": True,
        }

    async def generate_alt_texts(
        self, focus_keyword: str, images: list[dict]
    ) -> dict:
        alt_texts = []
        for img in images:
            heading = img.get("section_heading", "section")
            alt_texts.append(f"Mock alt text for {heading}")
        return {"alt_texts": alt_texts, "_mock": True}

    async def generate_image(self, prompt: str) -> bytes:
        return b"\x00" * 100  # 100 bytes fake PNG
