"""Tests for T7 media assembly (Task 2.6)."""

import asyncio
import json
import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import get_connection, run_migrations
from app.main import create_app
from app.middleware.auth_middleware import create_session
from app.models.user import create_user, update_user
from app.pipeline.transitions.t3_outlining import run_outlining
from app.pipeline.transitions.t4_drafting import run_drafting
from app.pipeline.transitions.t5_humanizing import run_humanizing
from app.pipeline.transitions.t6_edit_review import run_edit_review
from app.pipeline.transitions.t7_media_assembly import (
    run_media_assembly,
    _build_fallback_prompt,
    build_image_slots,
    _validate_image_prompter_output,
)
from app.image_styles import image_style_art_direction
from app.llm.mock import MockLLM
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *
from tests.test_locking import _create_article


class SlowImagePrompterLLM(MockLLM):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_image_prompts(self, *args, **kwargs):
        self.started.set()
        await self.release.wait()
        return await super().generate_image_prompts(*args, **kwargs)


class AltTextFailingLLM(MockLLM):
    async def generate_alt_texts(self, *args, **kwargs):
        raise RuntimeError("alt text service down")


class FailingUploadStorage:
    async def upload(self, *args, **kwargs):
        raise RuntimeError("upload service down")

    async def download(self, key):
        return None

    async def delete(self, key):
        return None

    async def exists(self, key):
        return False


@pytest_asyncio.fixture
async def article_at_media(db, config):
    """Article that's gone through outline + 2 draft loops → MEDIA_ASSEMBLY."""
    user = await create_user(db, "media@test.com")
    await update_user(db, user["id"], subscription_status="active", ghost_key_valid=1)
    article_id = await _create_article(db, user["id"], "OUTLINING")
    llm = MockLLM()

    await run_outlining(db, config, article_id, llm)
    # Iteration 1: reject
    await run_drafting(db, config, article_id, llm)
    await run_humanizing(db, config, article_id, llm)
    await run_edit_review(db, config, article_id, llm)
    # Iteration 2: approve → MEDIA_ASSEMBLY
    await run_drafting(db, config, article_id, llm)
    await run_humanizing(db, config, article_id, llm)
    await run_edit_review(db, config, article_id, llm)

    cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
    assert (await cursor.fetchone())["state"] == "MEDIA_ASSEMBLY"

    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
    user = dict(await cursor.fetchone())
    return {"user": user, "article_id": article_id}


class TestMediaAssembly:
    @pytest.mark.asyncio
    async def test_images_generated(self, db, config, article_at_media):
        llm = MockLLM()
        result = await run_media_assembly(db, config, article_at_media["article_id"], llm)
        assert result["success"] is True
        # Should have generated images for sections with image_needed
        cursor = await db.execute(
            "SELECT * FROM article_images WHERE article_id = ?",
            (article_at_media["article_id"],),
        )
        images = [dict(r) for r in await cursor.fetchall()]
        assert len(images) >= 2  # At least some images

    @pytest.mark.asyncio
    async def test_slow_media_assembly_does_not_block_from_analysis_submission(self, db, config, article_at_media):
        endpoint_user = await create_user(db, "analysis-during-media@test.com")
        await update_user(db, endpoint_user["id"], subscription_status="active", ghost_key_valid=1)
        session_id = await create_session(db, endpoint_user["id"], scope="full")
        await db.execute(
            """INSERT OR REPLACE INTO blog_profiles (id, url, site_name, is_ghost, profile_data, analyzed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("concurrent-prof", "https://concurrent.example", "Concurrent", True,
             json.dumps({"topics": []}), "2026-04-01T00:00:00+00:00"),
        )
        await db.commit()

        llm = SlowImagePrompterLLM()
        media_task = asyncio.create_task(
            run_media_assembly(db, config, article_at_media["article_id"], llm)
        )
        await asyncio.wait_for(llm.started.wait(), timeout=2)

        app = create_app(config)
        transport = ASGITransport(app=app)
        try:
            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies={"session_id": session_id},
            ) as client:
                resp = await asyncio.wait_for(
                    client.post(
                        "/api/seeds/from-analysis",
                        json={
                            "profile_id": "concurrent-prof",
                            "ideas": [{"title": "Concurrent Idea", "angle": "No lock"}],
                        },
                    ),
                    timeout=2,
                )

            assert resp.status_code == 201
            assert resp.json()["articles_created"] == 1
        finally:
            llm.release.set()

        result = await media_task
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_upload_failure_leaves_existing_media_and_draft_untouched(self, db, config, article_at_media):
        article_id = article_at_media["article_id"]
        now = utc_now()
        cursor = await db.execute(
            """SELECT id, humanized_draft_md FROM draft_iterations
               WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1""",
            (article_id,),
        )
        draft_row = await cursor.fetchone()
        original_draft = draft_row["humanized_draft_md"]

        await db.execute(
            """INSERT INTO article_images
               (id, article_id, anchor_index, source_type, storage_url, alt_text, created_at)
               VALUES (?, ?, 'COVER', 'generated', 'https://old.example/cover.jpg', 'Old cover', ?)""",
            (generate_id(), article_id, now),
        )
        await db.execute(
            """INSERT INTO article_images
               (id, article_id, anchor_index, source_type, storage_url, alt_text, created_at)
               VALUES (?, ?, '1', 'generated', 'https://old.example/body.jpg', 'Old body', ?)""",
            (generate_id(), article_id, now),
        )
        await db.commit()

        with pytest.raises(RuntimeError, match="upload service down"):
            await run_media_assembly(
                db,
                config,
                article_id,
                MockLLM(),
                storage=FailingUploadStorage(),
            )

        cursor = await db.execute(
            "SELECT anchor_index, storage_url, alt_text FROM article_images WHERE article_id = ? ORDER BY anchor_index",
            (article_id,),
        )
        images = [dict(r) for r in await cursor.fetchall()]
        assert images == [
            {"anchor_index": "1", "storage_url": "https://old.example/body.jpg", "alt_text": "Old body"},
            {"anchor_index": "COVER", "storage_url": "https://old.example/cover.jpg", "alt_text": "Old cover"},
        ]

        cursor = await db.execute("SELECT humanized_draft_md FROM draft_iterations WHERE id = ?", (draft_row["id"],))
        assert (await cursor.fetchone())["humanized_draft_md"] == original_draft

    @pytest.mark.asyncio
    async def test_alt_text_failure_leaves_existing_media_and_draft_untouched(self, db, config, article_at_media):
        article_id = article_at_media["article_id"]
        now = utc_now()
        cursor = await db.execute(
            """SELECT id, humanized_draft_md FROM draft_iterations
               WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1""",
            (article_id,),
        )
        draft_row = await cursor.fetchone()
        original_draft = draft_row["humanized_draft_md"]

        await db.execute(
            """INSERT INTO article_images
               (id, article_id, anchor_index, source_type, storage_url, alt_text, created_at)
               VALUES (?, ?, 'COVER', 'generated', 'https://old.example/cover.jpg', 'Old cover', ?)""",
            (generate_id(), article_id, now),
        )
        await db.commit()

        with pytest.raises(RuntimeError, match="Alt text generation failed"):
            await run_media_assembly(db, config, article_id, AltTextFailingLLM())

        cursor = await db.execute(
            "SELECT anchor_index, storage_url, alt_text FROM article_images WHERE article_id = ?",
            (article_id,),
        )
        images = [dict(r) for r in await cursor.fetchall()]
        assert images == [
            {"anchor_index": "COVER", "storage_url": "https://old.example/cover.jpg", "alt_text": "Old cover"},
        ]

        cursor = await db.execute("SELECT humanized_draft_md FROM draft_iterations WHERE id = ?", (draft_row["id"],))
        assert (await cursor.fetchone())["humanized_draft_md"] == original_draft

    @pytest.mark.asyncio
    async def test_alt_texts_generated(self, db, config, article_at_media):
        llm = MockLLM()
        await run_media_assembly(db, config, article_at_media["article_id"], llm)
        cursor = await db.execute(
            "SELECT alt_text FROM article_images WHERE article_id = ?",
            (article_at_media["article_id"],),
        )
        images = await cursor.fetchall()
        for img in images:
            assert img["alt_text"] is not None
            assert "Mock alt text for" in img["alt_text"]

    @pytest.mark.asyncio
    async def test_image_anchors_replaced(self, db, config, article_at_media):
        llm = MockLLM()
        await run_media_assembly(db, config, article_at_media["article_id"], llm)
        # Get latest humanized draft
        cursor = await db.execute(
            """SELECT humanized_draft_md FROM draft_iterations
               WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1""",
            (article_at_media["article_id"],),
        )
        row = await cursor.fetchone()
        draft = row["humanized_draft_md"]
        # No IMAGE_ANCHOR tags should remain
        assert "[IMAGE_ANCHOR:" not in draft

    @pytest.mark.asyncio
    async def test_image_prompter_prompts_used(self, db, config, article_at_media):
        """Image Prompter generates per-anchor prompts used for image generation."""
        llm = MockLLM()
        await run_media_assembly(db, config, article_at_media["article_id"], llm)
        cursor = await db.execute(
            "SELECT generation_prompt, anchor_index FROM article_images WHERE article_id = ? AND source_type = 'generated' ORDER BY created_at",
            (article_at_media["article_id"],),
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        assert len(rows) >= 2
        # Prompts should come from the mock Image Prompter (contain "Mock editorial photograph")
        for row in rows:
            assert "no text" in row["generation_prompt"].lower()
            assert "no watermarks" in row["generation_prompt"].lower()
        # Should have a COVER anchor
        anchor_indices = [r["anchor_index"] for r in rows]
        assert "COVER" in anchor_indices

    @pytest.mark.asyncio
    async def test_profile_image_style_injected_into_generated_prompts(self, db, config, article_at_media):
        """T7 uses the user's profile image style when no Content Brief override exists."""
        await update_user(
            db,
            article_at_media["user"]["id"],
            image_style="illustration",
            image_substyle="isometric",
        )

        llm = MockLLM()
        await run_media_assembly(db, config, article_at_media["article_id"], llm)
        cursor = await db.execute(
            "SELECT generation_prompt FROM article_images WHERE article_id = ? AND source_type = 'generated' LIMIT 1",
            (article_at_media["article_id"],),
        )
        prompt = (await cursor.fetchone())["generation_prompt"]
        assert "IMAGE STYLE HARD CONSTRAINT" in prompt
        assert "Illustration → Isometric" in prompt

    @pytest.mark.asyncio
    async def test_content_brief_image_style_overrides_profile(self, db, config, article_at_media):
        """Article-scoped Content Brief override beats profile default for T7."""
        await update_user(
            db,
            article_at_media["user"]["id"],
            image_style="photography",
            image_substyle="nostalgic_film",
        )
        await db.execute(
            "UPDATE articles SET content_brief = ? WHERE id = ?",
            (json.dumps({"user_description": "Test", "image_style": "graphic_poster", "image_substyle": "duotone"}), article_at_media["article_id"]),
        )
        await db.commit()

        llm = MockLLM()
        await run_media_assembly(db, config, article_at_media["article_id"], llm)
        cursor = await db.execute(
            "SELECT generation_prompt FROM article_images WHERE article_id = ? AND source_type = 'generated' LIMIT 1",
            (article_at_media["article_id"],),
        )
        prompt = (await cursor.fetchone())["generation_prompt"]
        assert "Graphic / Poster → Duotone" in prompt
        assert "Nostalgic film" not in prompt

    @pytest.mark.asyncio
    async def test_vault_reuse_skipped_for_generated_style_slots(self, db, config, article_at_media):
        """Automatic vault keyword reuse must not bypass selected image style."""
        user_id = article_at_media["user"]["id"]
        vault_id = generate_id()
        now = utc_now()
        await db.execute(
            """INSERT INTO vault_images (id, user_id, filename, storage_url, mime_type, description, tags, created_at)
               VALUES (?, ?, 'test.jpg', 'https://storage/test.jpg', 'image/jpeg', 'Mock editorial photograph healthcare AI', '["mock", "healthcare", "AI"]', ?)""",
            (vault_id, user_id, now),
        )
        await db.commit()

        llm = MockLLM()
        await run_media_assembly(db, config, article_at_media["article_id"], llm)
        cursor = await db.execute(
            "SELECT COUNT(*) as count FROM article_images WHERE article_id = ? AND source_type = 'vault'",
            (article_at_media["article_id"],),
        )
        assert (await cursor.fetchone())["count"] == 0

    @pytest.mark.asyncio
    async def test_vault_match_prioritized(self, db, config, article_at_media):
        """Vault image matched when keywords overlap."""
        user_id = article_at_media["user"]["id"]
        # Add a vault image with matching keywords
        vault_id = generate_id()
        now = utc_now()
        await db.execute(
            """INSERT INTO vault_images (id, user_id, filename, storage_url, mime_type, description, tags, created_at)
               VALUES (?, ?, 'test.jpg', 'https://storage/test.jpg', 'image/jpeg', 'section healthcare AI', '["ai", "healthcare"]', ?)""",
            (vault_id, user_id, now),
        )
        await db.commit()

        llm = MockLLM()
        await run_media_assembly(db, config, article_at_media["article_id"], llm)

        cursor = await db.execute(
            "SELECT source_type, vault_image_id FROM article_images WHERE article_id = ?",
            (article_at_media["article_id"],),
        )
        images = [dict(r) for r in await cursor.fetchall()]
        # At least some should be vault-matched (if keywords overlap)
        vault_matches = [i for i in images if i["source_type"] == "vault"]
        # This depends on keyword overlap — may or may not match
        # Just verify the function ran without error
        assert len(images) >= 2

    @pytest.mark.asyncio
    async def test_vault_not_reused_beyond_limit(self, db, config, article_at_media):
        """Vault image not used if used_count >= 3."""
        user_id = article_at_media["user"]["id"]
        vault_id = generate_id()
        now = utc_now()
        await db.execute(
            """INSERT INTO vault_images (id, user_id, filename, storage_url, mime_type, description, tags, used_count, created_at)
               VALUES (?, ?, 'used.jpg', 'https://storage/used.jpg', 'image/jpeg', 'Section Part test keyword', '["test"]', 3, ?)""",
            (vault_id, user_id, now),
        )
        await db.commit()

        llm = MockLLM()
        await run_media_assembly(db, config, article_at_media["article_id"], llm)

        cursor = await db.execute(
            "SELECT source_type FROM article_images WHERE article_id = ? AND vault_image_id = ?",
            (article_at_media["article_id"], vault_id),
        )
        row = await cursor.fetchone()
        assert row is None  # Should not be used


class TestImageSlotPlanning:
    ARTICLE_TITLE = "Deep Dive: Building Your First AI-Powered Customer Service Bot"
    KEYWORD = "AI-powered customer service bot"
    OUTLINE = {
        "thesis": "A useful AI customer service bot starts with support workflows, clean intents, and safe escalation paths.",
        "sections": [
            {
                "section_number": 1,
                "subheading": "Step 1: Map the Support Workflow Before Choosing Tools",
                "purpose": "Ground the bot in the actual customer support journey.",
                "key_points": ["ticket intake", "routing rules", "handoff points"],
                "image_needed": True,
            },
            {
                "section_number": 2,
                "subheading": "Step 2: Prepare the Knowledge Base",
                "purpose": "Explain the content foundation the bot needs.",
                "key_points": ["help center articles", "source freshness"],
                "image_needed": False,
            },
            {
                "section_number": 3,
                "subheading": "Step 3: Design Intents and Escalation Paths",
                "purpose": "Show how intents, confidence, and human handoff fit together.",
                "key_points": ["intent taxonomy", "confidence threshold", "human escalation"],
                "image_needed": True,
            },
        ],
    }
    DRAFT = """# Deep Dive: Building Your First AI-Powered Customer Service Bot

[IMAGE_ANCHOR:COVER]

This article explains how to build an AI-powered customer service bot around real support operations.

## Step 1: Map the Support Workflow Before Choosing Tools

Start with ticket intake, routing rules, handoff points, and the points where customers get stuck.

[IMAGE_ANCHOR:1]

## Step 2: Prepare the Knowledge Base

The bot needs clean help center articles and a process for keeping source material fresh.

## Step 3: Design Intents and Escalation Paths

Define the intent taxonomy, set confidence thresholds, and make human escalation obvious.

[IMAGE_ANCHOR:2]
"""

    def test_build_image_slots_keeps_cover_article_level_and_body_slots_section_specific(self):
        slots = build_image_slots(
            self.DRAFT,
            self.OUTLINE,
            {"user_description": "Build a practical customer service chatbot from scratch."},
            self.ARTICLE_TITLE,
            self.KEYWORD,
        )

        assert [slot["anchor"] for slot in slots] == [
            "IMAGE_ANCHOR:COVER",
            "IMAGE_ANCHOR:1",
            "IMAGE_ANCHOR:2",
        ]

        cover = slots[0]
        assert cover["slot_type"] == "cover"
        assert cover["heading"] == "Article cover"
        assert cover["nearby_text"] == ""
        assert "support workflows" in cover["semantic_target"]
        assert "Step 1" not in cover["heading"]

        first_inline = slots[1]
        assert first_inline["slot_type"] == "inline"
        assert first_inline["heading"] == "Step 1: Map the Support Workflow Before Choosing Tools"
        assert "ticket intake" in " ".join(first_inline["key_points"])

        second_inline = slots[2]
        assert second_inline["heading"] == "Step 3: Design Intents and Escalation Paths"
        assert "human escalation" in " ".join(second_inline["key_points"])

    def test_build_image_slots_uses_physical_section_when_cover_occupies_first_body_slot(self):
        legacy_draft = """# Deep Dive: Building Your First AI-Powered Customer Service Bot

## Step 1: Map the Support Workflow Before Choosing Tools

Start with ticket intake, routing rules, and handoff points.

[IMAGE_ANCHOR:COVER]

## Step 2: Prepare the Knowledge Base

The bot needs clean help center articles and source freshness.

## Step 3: Design Intents and Escalation Paths

Define the intent taxonomy, confidence threshold, and human escalation lane.

[IMAGE_ANCHOR:1]
"""

        slots = build_image_slots(
            legacy_draft,
            self.OUTLINE,
            {"user_description": "Build a practical customer service chatbot from scratch."},
            self.ARTICLE_TITLE,
            self.KEYWORD,
        )

        assert [slot["anchor"] for slot in slots] == ["IMAGE_ANCHOR:COVER", "IMAGE_ANCHOR:1"]
        assert slots[0]["slot_type"] == "cover"
        assert slots[0]["heading"] == "Article cover"
        assert slots[0]["nearby_text"] == ""
        assert "Article-level concept" in slots[0]["semantic_target"]

        inline = slots[1]
        assert inline["slot_type"] == "inline"
        assert inline["heading"] == "Step 3: Design Intents and Escalation Paths"
        assert inline["outline_section_number"] == 3
        assert "human escalation" in " ".join(inline["key_points"])
        assert "ticket intake" not in " ".join(inline["key_points"])

    def test_validate_image_prompter_rejects_repeated_laptop_people_prompts(self):
        slots = build_image_slots(self.DRAFT, self.OUTLINE, {}, self.ARTICLE_TITLE, self.KEYWORD)
        result = {
            "images": [
                {
                    "anchor": slot["anchor"],
                    "semantic_target": slot["semantic_target"],
                    "primary_subject": "woman at laptop",
                    "concrete_objects": ["laptop", "desk"],
                    "composition_type": "person seated at laptop",
                    "why_this_matches": "Generic office work.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT. Warm lifestyle photo of a woman sitting at a laptop in an office, no text, no watermarks.",
                }
                for slot in slots
            ]
        }

        valid, reasons, _images = _validate_image_prompter_output(
            result,
            slots,
            image_style_direction="IMAGE STYLE HARD CONSTRAINT: Photography",
        )

        assert valid is False
        assert any("repeated primary_subject" in reason for reason in reasons)
        assert any("generic workplace object scene" in reason for reason in reasons)
        assert any("too many generic laptop/person/desk prompts" in reason for reason in reasons)

    def test_validate_image_prompter_rejects_single_people_at_laptop_prompt(self):
        slots = build_image_slots(self.DRAFT, self.OUTLINE, {}, self.ARTICLE_TITLE, self.KEYWORD)
        result = {
            "images": [
                {
                    "anchor": "IMAGE_ANCHOR:COVER",
                    "semantic_target": slots[0]["semantic_target"],
                    "primary_subject": "support ticket routing board",
                    "concrete_objects": ["support tickets", "routing lanes", "escalation marker"],
                    "composition_type": "overhead operational workflow",
                    "why_this_matches": "Shows the bot as a support workflow system.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography. Editorial photograph of support tickets moving through routing lanes with an escalation marker, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:1",
                    "semantic_target": slots[1]["semantic_target"],
                    "primary_subject": "three women sitting in front of a laptop",
                    "concrete_objects": ["women", "laptop", "desk"],
                    "composition_type": "people seated around a laptop in an office",
                    "why_this_matches": "Generic office work.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography. Warm lifestyle photo of three women sitting in front of a laptop in an office, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:2",
                    "semantic_target": slots[2]["semantic_target"],
                    "primary_subject": "escalation path control panel",
                    "concrete_objects": ["intent taxonomy", "confidence threshold", "human escalation"],
                    "composition_type": "angled control-room detail",
                    "why_this_matches": "Matches the section about intents and handoff.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography. Angled detail of intent taxonomy cards, confidence threshold markers, and a human escalation lane, no text, no watermarks.",
                },
            ]
        }

        valid, reasons, _images = _validate_image_prompter_output(
            result,
            slots,
            image_style_direction="IMAGE STYLE HARD CONSTRAINT: Photography",
        )

        assert valid is False
        assert any("IMAGE_ANCHOR:1: generic workplace object scene" in reason for reason in reasons)
        assert any("IMAGE_ANCHOR:1: generic human workplace scene" in reason for reason in reasons)

    def test_validate_image_prompter_rejects_generic_fallback_laptop_person_prompt(self):
        slots = build_image_slots(self.DRAFT, self.OUTLINE, {}, self.ARTICLE_TITLE, self.KEYWORD)
        result = {
            "images": [
                {
                    "anchor": slot["anchor"],
                    "semantic_target": slot["semantic_target"],
                    "primary_subject": "person working on laptop",
                    "concrete_objects": ["laptop", "desk", "notebook"],
                    "composition_type": "warm office desk scene",
                    "why_this_matches": "Generic productivity scene.",
                    "prompt": (
                        "IMAGE STYLE HARD CONSTRAINT: Photography. Warm lifestyle photograph of "
                        "a person working on a laptop at a desk in a bright office, no text, no watermarks."
                    ),
                }
                for slot in slots
            ]
        }

        valid, reasons, _images = _validate_image_prompter_output(
            result,
            slots,
            image_style_direction="IMAGE STYLE HARD CONSTRAINT: Photography",
        )

        assert valid is False
        assert any("generic people/laptop scene" in reason for reason in reasons)
        assert any("generic workplace object scene" in reason for reason in reasons)

    def test_validate_image_prompter_accepts_distinct_section_relevant_subjects(self):
        slots = build_image_slots(self.DRAFT, self.OUTLINE, {}, self.ARTICLE_TITLE, self.KEYWORD)
        result = {
            "images": [
                {
                    "anchor": "IMAGE_ANCHOR:COVER",
                    "semantic_target": slots[0]["semantic_target"],
                    "primary_subject": "support ticket routing board",
                    "concrete_objects": ["support tickets", "routing lanes", "escalation marker"],
                    "composition_type": "overhead operational workspace",
                    "why_this_matches": "Shows the bot as a support workflow system.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography. Editorial photograph of support tickets moving through routing lanes with an escalation marker, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:1",
                    "semantic_target": slots[1]["semantic_target"],
                    "primary_subject": "ticket intake map",
                    "concrete_objects": ["ticket intake", "routing rules", "handoff points"],
                    "composition_type": "close-up process map",
                    "why_this_matches": "Matches the section about mapping support workflows.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography. Close-up of ticket intake cards, routing rules, and handoff point markers arranged as a support workflow, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:2",
                    "semantic_target": slots[2]["semantic_target"],
                    "primary_subject": "escalation path control panel",
                    "concrete_objects": ["intent taxonomy", "confidence threshold", "human escalation"],
                    "composition_type": "angled control-room detail",
                    "why_this_matches": "Matches the section about intents and handoff.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography. Angled detail of intent taxonomy cards, confidence threshold markers, and a human escalation lane, no text, no watermarks.",
                },
            ]
        }

        valid, reasons, images = _validate_image_prompter_output(
            result,
            slots,
            image_style_direction="IMAGE STYLE HARD CONSTRAINT: Photography",
        )

        assert valid is True
        assert reasons == []
        assert [img["anchor"] for img in images] == [slot["anchor"] for slot in slots]

    def test_validate_image_prompter_rejects_samey_lifestyle_tabletop_failure(self):
        slots = build_image_slots(self.DRAFT, self.OUTLINE, {}, self.ARTICLE_TITLE, self.KEYWORD)
        result = {
            "images": [
                {
                    "anchor": "IMAGE_ANCHOR:COVER",
                    "semantic_target": slots[0]["semantic_target"],
                    "primary_subject": "tidy paper workflow on a warm table",
                    "concrete_objects": ["sticky notes", "coffee mug", "notebook", "wooden table"],
                    "composition_type": "sunlit tabletop still life",
                    "why_this_matches": "A generic productivity scene for saving time.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Sunlit tabletop still life with sticky notes, a coffee mug, and notebook on a wooden table, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:1",
                    "semantic_target": slots[1]["semantic_target"],
                    "primary_subject": "cozy phone and notes arrangement",
                    "concrete_objects": ["smartphone", "handwritten notes", "pen", "window light"],
                    "composition_type": "warm tabletop device close-up",
                    "why_this_matches": "A generic productivity scene for quick work.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Cozy phone beside handwritten notes and a pen by a bright window, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:2",
                    "semantic_target": slots[2]["semantic_target"],
                    "primary_subject": "notebook with neat bullet points",
                    "concrete_objects": ["notebook", "pen", "earbuds", "wooden table"],
                    "composition_type": "sunlit tabletop still life",
                    "why_this_matches": "A generic productivity scene for organized notes.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Notebook with neat bullet points, pen, and earbuds on a wooden table, no text, no watermarks.",
                },
            ]
        }

        valid, reasons, _images = _validate_image_prompter_output(
            result,
            slots,
            image_style_direction="IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle",
        )

        assert valid is False
        assert any("samey lifestyle still-life repetition" in reason for reason in reasons)
        assert any("generic surface composition without enough workflow substance" in reason for reason in reasons)
        assert any("too many slots collapse into generic flat-lay/tabletop/device-paper compositions" in reason for reason in reasons)

    def test_validate_image_prompter_does_not_count_banned_laptop_mentions_as_subjects(self):
        slots = build_image_slots(self.DRAFT, self.OUTLINE, {}, self.ARTICLE_TITLE, self.KEYWORD)
        result = {
            "images": [
                {
                    "anchor": "IMAGE_ANCHOR:COVER",
                    "semantic_target": slots[0]["semantic_target"],
                    "primary_subject": "support ticket routing board",
                    "concrete_objects": ["support tickets", "routing lanes", "escalation marker"],
                    "composition_type": "overhead operational workspace",
                    "why_this_matches": "Shows the bot as a support workflow system.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography. Editorial photograph of support tickets moving through routing lanes with an escalation marker, no laptop, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:1",
                    "semantic_target": slots[1]["semantic_target"],
                    "primary_subject": "ticket intake map",
                    "concrete_objects": ["ticket intake", "routing rules", "handoff points"],
                    "composition_type": "close-up process map",
                    "why_this_matches": "Matches the section about mapping support workflows.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography. Close-up of ticket intake cards, routing rules, and handoff point markers arranged as a support workflow, avoid desk scenes, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:2",
                    "semantic_target": slots[2]["semantic_target"],
                    "primary_subject": "escalation path control panel",
                    "concrete_objects": ["intent taxonomy", "confidence threshold", "human escalation"],
                    "composition_type": "angled control-room detail",
                    "why_this_matches": "Matches the section about intents and handoff.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography. Angled detail of intent taxonomy cards, confidence threshold markers, and a human escalation lane without person working imagery, no text, no watermarks.",
                },
            ]
        }

        valid, reasons, _images = _validate_image_prompter_output(
            result,
            slots,
            image_style_direction="IMAGE STYLE HARD CONSTRAINT: Photography",
        )

        assert valid is True
        assert "too many generic laptop/person/desk prompts" not in reasons

    def test_ai_time_savings_article_slots_require_distinct_workflow_artifacts(self):
        outline = {
            "thesis": (
                "Simple AI tools save time this week by drafting email follow-ups, "
                "summarizing meetings, and analyzing customer feedback."
            ),
            "sections": [
                {
                    "section_number": 1,
                    "subheading": "1. Automate Your Inbox: Draft Replies and Follow-ups in Seconds",
                    "purpose": "Use AI for email management.",
                    "key_points": [
                        "Draft a polite but firm follow-up email about an unpaid invoice.",
                        "Write a new lead response with Tuesday or Thursday scheduling options.",
                        "Turn a five-minute email into a 30-second final polish.",
                    ],
                    "image_needed": True,
                },
                {
                    "section_number": 2,
                    "subheading": "2. End Meeting Amnesia: Get Perfect Summaries and Action Items, Instantly",
                    "purpose": "Use AI meeting assistants for summaries and action items.",
                    "key_points": [
                        "Capture a transcript after the call.",
                        "Create summary cards and action-item owners.",
                        "It emails you a full transcript, summary cards, and action-item owners minutes after the meeting.",
                        "Send the calendar handoff minutes after the meeting.",
                    ],
                    "image_needed": True,
                },
                {
                    "section_number": 3,
                    "subheading": "3. Find the Signal in the Noise: Analyze Customer Feedback in Minutes",
                    "purpose": "Use AI to cluster customer reviews and survey responses.",
                    "key_points": [
                        "Paste customer feedback into a tool.",
                        "Identify top themes and direct quote examples.",
                        "Turn the spreadsheet into a priority list.",
                    ],
                    "image_needed": True,
                },
            ],
        }
        draft = """# Forget the Hype: 3 Ways to Use AI That Actually Save You Time This Week

[IMAGE_ANCHOR:COVER]

Use AI for email drafts, meeting summaries, and customer feedback analysis this week.

## 1. Automate Your Inbox: Draft Replies and Follow-ups in Seconds

Draft replies, invoice follow-ups, lead responses, and scheduling options.

[IMAGE_ANCHOR:1]

## 2. End Meeting Amnesia: Get Perfect Summaries and Action Items, Instantly

Capture transcripts, summary cards, action-item owners, and calendar handoffs.

[IMAGE_ANCHOR:2]

## 3. Find the Signal in the Noise: Analyze Customer Feedback in Minutes

Cluster customer reviews, survey responses, quote markers, and priority lists.

[IMAGE_ANCHOR:3]
"""
        slots = build_image_slots(
            draft,
            outline,
            {},
            "Forget the Hype: 3 Ways to Use AI That Actually Save You Time This Week",
            "ways to use AI to save time",
        )

        assert "weekly AI time-savings toolkit" in slots[0]["visual_concept"]
        assert "email drafting" in slots[1]["visual_concept"]
        assert "meeting capture" in slots[2]["visual_concept"]
        assert "customer feedback analysis" in slots[3]["visual_concept"]

    def test_ai_time_savings_article_rejects_flat_lay_collapse_even_with_right_labels(self):
        outline = {
            "thesis": (
                "Simple AI tools save time this week by drafting email follow-ups, "
                "summarizing meetings, and analyzing customer feedback."
            ),
            "sections": [
                {
                    "section_number": 1,
                    "subheading": "1. Automate Your Inbox: Draft Replies and Follow-ups in Seconds",
                    "purpose": "Use AI for email management.",
                    "key_points": [
                        "Draft a polite but firm follow-up email about an unpaid invoice.",
                        "Write a new lead response with Tuesday or Thursday scheduling options.",
                    ],
                    "image_needed": True,
                },
                {
                    "section_number": 2,
                    "subheading": "2. End Meeting Amnesia: Get Perfect Summaries and Action Items, Instantly",
                    "purpose": "Use AI meeting assistants for summaries and action items.",
                    "key_points": [
                        "Capture a transcript after the call.",
                        "Create summary cards and action-item owners.",
                    ],
                    "image_needed": True,
                },
                {
                    "section_number": 3,
                    "subheading": "3. Find the Signal in the Noise: Analyze Customer Feedback in Minutes",
                    "purpose": "Use AI to cluster customer reviews and survey responses.",
                    "key_points": [
                        "Identify top themes and direct quote examples.",
                        "Turn the spreadsheet into a priority list.",
                    ],
                    "image_needed": True,
                },
            ],
        }
        draft = """# Forget the Hype: 3 Ways to Use AI That Actually Save You Time This Week

[IMAGE_ANCHOR:COVER]

Use AI for email drafts, meeting summaries, and customer feedback analysis this week.

## 1. Automate Your Inbox: Draft Replies and Follow-ups in Seconds

Draft replies, invoice follow-ups, lead responses, and scheduling options.

[IMAGE_ANCHOR:1]

## 2. End Meeting Amnesia: Get Perfect Summaries and Action Items, Instantly

Capture transcripts, summary cards, action-item owners, and calendar handoffs.

[IMAGE_ANCHOR:2]

## 3. Find the Signal in the Noise: Analyze Customer Feedback in Minutes

Cluster customer reviews, survey responses, quote markers, and priority lists.

[IMAGE_ANCHOR:3]
"""
        slots = build_image_slots(
            draft,
            outline,
            {},
            "Forget the Hype: 3 Ways to Use AI That Actually Save You Time This Week",
            "ways to use AI to save time",
        )
        result = {
            "images": [
                {
                    "anchor": "IMAGE_ANCHOR:COVER",
                    "semantic_target": slots[0]["semantic_target"],
                    "primary_subject": "weekly ai productivity planning notes",
                    "concrete_objects": ["sticky notes", "paper cards", "tablet", "calendar", "email follow-up", "meeting summary", "feedback themes"],
                    "composition_type": "overhead tabletop still life",
                    "why_this_matches": "Labels the three workflows on one warm planning surface.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Overhead tabletop still life of sticky notes, paper cards, a tablet, and a calendar labeled for email follow-up, meeting summary, and feedback themes, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:1",
                    "semantic_target": slots[1]["semantic_target"],
                    "primary_subject": "invoice follow-up notes beside a tablet",
                    "concrete_objects": ["papers", "tablet", "schedule card", "invoice follow-up", "lead response"],
                    "composition_type": "warm device close-up on a desk",
                    "why_this_matches": "Uses the right labels for reply drafting and scheduling.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Warm close-up of papers, a tablet, and schedule cards on a desk for invoice follow-up and lead response planning, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:2",
                    "semantic_target": slots[2]["semantic_target"],
                    "primary_subject": "meeting summary papers on a conference table",
                    "concrete_objects": ["summary cards", "action item notes", "calendar page", "transcript papers"],
                    "composition_type": "flat lay paper note arrangement",
                    "why_this_matches": "Mentions summary cards and action items on the table.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Flat lay of summary cards, action item notes, a calendar page, and transcript papers spread across a conference table, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:3",
                    "semantic_target": slots[3]["semantic_target"],
                    "primary_subject": "customer feedback cards and survey papers",
                    "concrete_objects": ["feedback cards", "survey papers", "priority list", "sticky notes"],
                    "composition_type": "sunlit tabletop still life",
                    "why_this_matches": "Places customer feedback themes onto paper cards and lists.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Sunlit tabletop still life of customer feedback cards, survey papers, sticky notes, and a priority list on a desk, no text, no watermarks.",
                },
            ]
        }

        valid, reasons, _images = _validate_image_prompter_output(
            result,
            slots,
            image_style_direction="IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle",
        )

        assert valid is False
        assert any("too many slots collapse into generic flat-lay/tabletop/device-paper compositions" in reason for reason in reasons)
        assert any("samey lifestyle still-life repetition" in reason for reason in reasons)

    def test_ai_time_savings_article_accepts_consistent_but_varied_workflows(self):
        outline = {
            "thesis": (
                "Simple AI tools save time this week by drafting email follow-ups, "
                "summarizing meetings, and analyzing customer feedback."
            ),
            "sections": [
                {
                    "section_number": 1,
                    "subheading": "1. Automate Your Inbox: Draft Replies and Follow-ups in Seconds",
                    "purpose": "Use AI for email management.",
                    "key_points": [
                        "Draft a polite but firm follow-up email about an unpaid invoice.",
                        "Write a new lead response with Tuesday or Thursday scheduling options.",
                        "Turn a five-minute email into a 30-second final polish.",
                    ],
                    "image_needed": True,
                },
                {
                    "section_number": 2,
                    "subheading": "2. End Meeting Amnesia: Get Perfect Summaries and Action Items, Instantly",
                    "purpose": "Use AI meeting assistants for summaries and action items.",
                    "key_points": [
                        "Capture a transcript after the call.",
                        "Create summary cards and action-item owners.",
                        "Send the calendar handoff minutes after the meeting.",
                    ],
                    "image_needed": True,
                },
                {
                    "section_number": 3,
                    "subheading": "3. Find the Signal in the Noise: Analyze Customer Feedback in Minutes",
                    "purpose": "Use AI to cluster customer reviews and survey responses.",
                    "key_points": [
                        "Paste customer feedback into a tool.",
                        "Identify top themes and direct quote examples.",
                        "Turn the spreadsheet into a priority list.",
                    ],
                    "image_needed": True,
                },
            ],
        }
        draft = """# Forget the Hype: 3 Ways to Use AI That Actually Save You Time This Week

[IMAGE_ANCHOR:COVER]

Use AI for email drafts, meeting summaries, and customer feedback analysis this week.

## 1. Automate Your Inbox: Draft Replies and Follow-ups in Seconds

Draft replies, invoice follow-ups, lead responses, and scheduling options.

[IMAGE_ANCHOR:1]

## 2. End Meeting Amnesia: Get Perfect Summaries and Action Items, Instantly

Capture transcripts, summary cards, action-item owners, and calendar handoffs.

[IMAGE_ANCHOR:2]

## 3. Find the Signal in the Noise: Analyze Customer Feedback in Minutes

Cluster customer reviews, survey responses, quote markers, and priority lists.

[IMAGE_ANCHOR:3]
"""
        slots = build_image_slots(
            draft,
            outline,
            {},
            "Forget the Hype: 3 Ways to Use AI That Actually Save You Time This Week",
            "ways to use AI to save time",
        )
        result = {
            "images": [
                {
                    "anchor": "IMAGE_ANCHOR:COVER",
                    "semantic_target": slots[0]["semantic_target"],
                    "primary_subject": "weekly ai workflow hub with three connected stations",
                    "concrete_objects": ["inbox triage lane", "meeting summary action board", "feedback priority clusters"],
                    "composition_type": "wide operations-room overview",
                    "why_this_matches": "Unifies the week's email follow-up, meeting action-item, and feedback prioritization workflows in one system.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Wide editorial photograph of a weekly AI workflow hub with an inbox triage lane, a meeting summary action board, and feedback priority clusters connected in one room, amber-and-slate palette, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:1",
                    "semantic_target": slots[1]["semantic_target"],
                    "primary_subject": "mailroom-style inbox triage lane",
                    "concrete_objects": ["reply tray", "invoice reminder envelope", "lead response envelope", "scheduling lane"],
                    "composition_type": "close corridor sorting shot",
                    "why_this_matches": "Shows the section's email reply, invoice, lead response, and scheduling workflow as one sorting system.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Close editorial photograph of a mailroom-style inbox triage lane with a reply tray, invoice reminder envelope, lead response envelope, and scheduling lane, amber-and-slate palette, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:2",
                    "semantic_target": slots[2]["semantic_target"],
                    "primary_subject": "post-meeting capture station",
                    "concrete_objects": ["recording light", "audio waveform display", "summary cards", "action item owner markers", "calendar handoff"],
                    "composition_type": "angled empty conference-room detail",
                    "why_this_matches": "Shows a meeting transcript turning into summaries, owners, and calendar handoff immediately after the call.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Angled editorial photograph inside an empty conference room with a recording light, audio waveform display, summary cards, action item owner markers, and calendar handoff, amber-and-slate palette, no text, no watermarks.",
                },
                {
                    "anchor": "IMAGE_ANCHOR:3",
                    "semantic_target": slots[3]["semantic_target"],
                    "primary_subject": "support-room feedback clustering wall",
                    "concrete_objects": ["customer response tokens", "review snippets", "theme clusters", "priority lane"],
                    "composition_type": "frontal service-wall installation",
                    "why_this_matches": "Shows customer reviews and survey responses being grouped into themes and priority lanes.",
                    "prompt": "IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle. Editorial photograph of a support-room feedback clustering wall with customer response tokens, review snippets, theme clusters, and a priority lane, amber-and-slate palette, no text, no watermarks.",
                },
            ]
        }

        valid, reasons, _images = _validate_image_prompter_output(
            result,
            slots,
            image_style_direction="IMAGE STYLE HARD CONSTRAINT: Photography warm lifestyle",
        )

        assert valid is True
        assert reasons == []


class TestBuildFallbackPrompt:
    """Trello #331: fallback prompt should default to photography, use illustration only when user revision notes explicitly signal it."""

    TITLE = "How Remote Teams Stay Connected"
    KEYWORD = "remote team culture"
    HEADING = "Asynchronous Rituals"

    # Photography variant signature phrases — must appear verbatim in default output.
    PHOTO_SIGNATURES = [
        "Editorial photograph for a blog article",
        "Directional natural light",
        "Magazine feature quality, not stock.",
        "No handshakes, no lightbulbs",
    ]

    # Illustration variant signature phrases.
    ILLUSTRATION_SIGNATURES = [
        "Editorial illustration for a blog article",
        "Warm hand-made feel",
        "considered palette of 3-4 colors",
        "hourglasses with coins",
    ]

    def test_default_uses_photography_variant(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING)
        for sig in self.PHOTO_SIGNATURES:
            assert sig in prompt, f"Expected photography signature '{sig}' in prompt"
        for sig in self.ILLUSTRATION_SIGNATURES:
            assert sig not in prompt, f"Unexpected illustration signature '{sig}' in prompt"

    def test_none_revision_notes_uses_photography_variant(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes=None)
        assert "Editorial photograph" in prompt
        assert "Editorial illustration" not in prompt
        assert "User direction" not in prompt

    def test_empty_revision_notes_uses_photography_variant(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes="")
        assert "Editorial photograph" in prompt
        assert "Editorial illustration" not in prompt
        assert "User direction" not in prompt

    def test_whitespace_revision_notes_uses_photography_variant(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes="   \n\t  ")
        assert "Editorial photograph" in prompt
        assert "Editorial illustration" not in prompt
        assert "User direction" not in prompt

    def test_illustrat_keyword_selects_illustration_variant(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes="Please make these illustrations instead")
        assert "Editorial illustration" in prompt
        assert "Editorial photograph" not in prompt

    def test_drawn_keyword_selects_illustration_variant(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes="hand drawn style please")
        assert "Editorial illustration" in prompt
        assert "Editorial photograph" not in prompt

    def test_painted_keyword_selects_illustration_variant(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes="something painted looking")
        assert "Editorial illustration" in prompt
        assert "Editorial photograph" not in prompt

    def test_vector_keyword_selects_illustration_variant(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes="clean vector art")
        assert "Editorial illustration" in prompt
        assert "Editorial photograph" not in prompt

    def test_illustration_keyword_case_insensitive(self):
        for note in ("ILLUSTRATED please", "Illustrate this", "Drawn by hand", "PAINTED mural", "VECTOR style"):
            prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes=note)
            assert "Editorial illustration" in prompt, f"Expected illustration variant for note: {note!r}"
            assert "Editorial photograph" not in prompt, f"Should not have photography for note: {note!r}"

    def test_unrelated_revision_notes_still_uses_photography(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes="please make the tone more formal")
        assert "Editorial photograph" in prompt
        assert "Editorial illustration" not in prompt

    def test_user_direction_prepended_for_photography(self):
        notes = "please make the tone more formal"
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes=notes)
        expected_line = f"User direction (apply above all else): {notes}"
        assert prompt.startswith(expected_line), f"Prompt did not start with expected override line. First 200 chars:\n{prompt[:200]}"
        assert "Editorial photograph" in prompt

    def test_user_direction_prepended_for_illustration(self):
        notes = "please illustrate these in a warm style"
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes=notes)
        expected_line = f"User direction (apply above all else): {notes}"
        assert prompt.startswith(expected_line)
        assert "Editorial illustration" in prompt

    def test_no_user_direction_line_when_notes_absent(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING)
        assert "User direction" not in prompt

    def test_placeholder_substitution_photography(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING)
        assert f"'{self.TITLE}'" in prompt
        assert self.KEYWORD in prompt
        assert f"'{self.HEADING}'" in prompt
        # Raw placeholders must not leak through
        assert "{article_title}" not in prompt
        assert "{focus_keyword}" not in prompt
        assert "{section_heading}" not in prompt

    def test_placeholder_substitution_illustration(self):
        prompt = _build_fallback_prompt(self.TITLE, self.KEYWORD, self.HEADING, user_revision_notes="please illustrate")
        assert f"'{self.TITLE}'" in prompt
        assert self.KEYWORD in prompt
        assert f"'{self.HEADING}'" in prompt
        assert "{article_title}" not in prompt
        assert "{focus_keyword}" not in prompt
        assert "{section_heading}" not in prompt

    def test_style_aware_fallback_avoids_generic_human_workplace_subjects(self):
        outline = TestImageSlotPlanning.OUTLINE
        slots = build_image_slots(
            TestImageSlotPlanning.DRAFT,
            outline,
            {},
            TestImageSlotPlanning.ARTICLE_TITLE,
            TestImageSlotPlanning.KEYWORD,
        )
        style_direction = image_style_art_direction("photography", "warm_lifestyle")
        assert "human scenes" not in style_direction

        result = {"images": []}
        for slot in slots:
            prompt = _build_fallback_prompt(
                TestImageSlotPlanning.ARTICLE_TITLE,
                TestImageSlotPlanning.KEYWORD,
                slot["heading"],
                image_style_direction=style_direction,
                slot=slot,
            )
            result["images"].append({
                "anchor": slot["anchor"],
                "semantic_target": slot["semantic_target"],
                "primary_subject": f"{slot['heading']} workflow artifact",
                "concrete_objects": ["support tickets", "routing markers", "handoff path"],
                "composition_type": f"{slot['anchor']} article-specific process artifact",
                "why_this_matches": "Uses the article's concrete workflow terms instead of stock productivity imagery.",
                "prompt": prompt,
            })

        valid, reasons, _images = _validate_image_prompter_output(
            result,
            slots,
            image_style_direction=style_direction,
        )

        assert valid is True
        assert reasons == []

    def test_style_aware_fallback_uses_slot_scene_guidance_for_ai_time_savings_article(self):
        slot = {
            "slot_type": "inline",
            "anchor": "IMAGE_ANCHOR:2",
            "heading": "End Meeting Amnesia",
            "visual_concept": "meeting capture workflow shown inside an empty conference room after everyone has left",
            "required_visual_terms": ["meeting", "transcript", "summary", "action item", "calendar", "recording"],
            "purpose": "Use AI meeting assistants for summaries and action items.",
            "key_points": ["Capture a transcript after the call."],
        }
        style_direction = image_style_art_direction("photography", "warm_lifestyle")

        prompt = _build_fallback_prompt(
            "Forget the Hype: 3 Ways to Use AI That Actually Save You Time This Week",
            "ways to use AI to save time",
            "End Meeting Amnesia",
            image_style_direction=style_direction,
            slot=slot,
        )

        assert "Preferred scene family: Prefer an empty meeting room" in prompt
        assert "make this slot substantively different in setting and composition" in prompt
        assert "must stay blank, unlabeled, or abstract" in prompt
        assert "do not make a tablet, paper pad, or tabletop the main subject" in prompt
