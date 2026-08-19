"""Tests for T11 publishing (Task 2.8)."""

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.pipeline.transitions.t3_outlining import run_outlining
from app.pipeline.transitions.t4_drafting import run_drafting
from app.pipeline.transitions.t5_humanizing import run_humanizing
from app.pipeline.transitions.t6_edit_review import run_edit_review
from app.pipeline.transitions.t7_media_assembly import run_media_assembly
from app.pipeline.transitions.t8_to_checkpoint_2 import run_to_checkpoint_2
from app.pipeline.transitions.t11_publishing import run_publishing
from app.services.encryption import encrypt
from app.llm.mock import MockLLM
from app.services.email import get_sent_emails, clear_sent_emails
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *
from tests.test_locking import _create_article

MOCK_GHOST_KEY = "abc123def456:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb"


@pytest_asyncio.fixture
async def article_ready_to_publish(db, config):
    """Article at READY_TO_PUBLISH with Ghost configured."""
    clear_sent_emails()
    user = await create_user(db, "pub@test.com")
    await update_user(
        db, user["id"],
        subscription_status="active",
        ghost_key_valid=1,
        ghost_url="https://blog.example.com",
        ghost_admin_api_key=MOCK_GHOST_KEY,
    )
    article_id = await _create_article(db, user["id"], "OUTLINING")
    llm = MockLLM()

    await run_outlining(db, config, article_id, llm)
    await run_drafting(db, config, article_id, llm)
    await run_humanizing(db, config, article_id, llm)
    await run_edit_review(db, config, article_id, llm)
    await run_drafting(db, config, article_id, llm)
    await run_humanizing(db, config, article_id, llm)
    await run_edit_review(db, config, article_id, llm)
    await run_media_assembly(db, config, article_id, llm)
    await run_to_checkpoint_2(db, config, article_id)

    # Approve → READY_TO_PUBLISH
    now = utc_now()
    await db.execute(
        "UPDATE articles SET state = 'READY_TO_PUBLISH', scheduled_publish_at = ? WHERE id = ?",
        (now, article_id),
    )
    await db.execute(
        "UPDATE article_reviews SET status = 'approved' WHERE article_id = ?",
        (article_id,),
    )
    await db.commit()

    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
    return {"user": dict(await cursor.fetchone()), "article_id": article_id}


def _ghost_mocks():
    """Return patches for Ghost API calls."""
    mock_create = AsyncMock(return_value={
        "id": "ghost-post-001",
        "url": "https://blog.example.com/test-post/",
        "slug": "test-post",
    })
    mock_upload = AsyncMock(return_value="https://blog.example.com/content/images/test.webp")
    mock_dup = AsyncMock(return_value=None)
    mock_fetch = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")  # fake PNG bytes

    return (
        patch("app.pipeline.transitions.t11_publishing.create_ghost_post", mock_create),
        patch("app.pipeline.transitions.t11_publishing.upload_image_to_ghost", mock_upload),
        patch("app.pipeline.transitions.t11_publishing.check_duplicate_post", mock_dup),
        patch("app.pipeline.transitions.t11_publishing._fetch_image_bytes", mock_fetch),
        mock_create,
        mock_upload,
    )


class TestPublishing:
    @pytest.mark.asyncio
    async def test_full_publish(self, db, config, article_ready_to_publish):
        p1, p2, p3, p4, mock_create, _ = _ghost_mocks()
        with p1, p2, p3, p4:
            llm = MockLLM()
            result = await run_publishing(db, config, article_ready_to_publish["article_id"], llm)
            assert result["success"] is True
            assert "ghost_post_url" in result
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_transitions_to_published(self, db, config, article_ready_to_publish):
        p1, p2, p3, p4, _, _ = _ghost_mocks()
        with p1, p2, p3, p4:
            llm = MockLLM()
            await run_publishing(db, config, article_ready_to_publish["article_id"], llm)
            cursor = await db.execute(
                "SELECT state, ghost_post_id, ghost_post_url, published_at FROM articles WHERE id = ?",
                (article_ready_to_publish["article_id"],),
            )
            row = await cursor.fetchone()
            assert row["state"] == "PUBLISHED"
            assert row["ghost_post_id"] == "ghost-post-001"
            assert "blog.example.com" in row["ghost_post_url"]
            assert row["published_at"] is not None

    @pytest.mark.asyncio
    async def test_notification_email_sent(self, db, config, article_ready_to_publish):
        clear_sent_emails()
        p1, p2, p3, p4, _, _ = _ghost_mocks()
        with p1, p2, p3, p4:
            llm = MockLLM()
            await run_publishing(db, config, article_ready_to_publish["article_id"], llm)
            emails = get_sent_emails()
            notif = [e for e in emails if e.get("purpose") == "publish_notification"]
            assert len(notif) >= 1

    @pytest.mark.asyncio
    async def test_images_uploaded_to_ghost(self, db, config, article_ready_to_publish):
        p1, p2, p3, p4, _, mock_upload = _ghost_mocks()
        with p1, p2, p3, p4:
            llm = MockLLM()
            await run_publishing(db, config, article_ready_to_publish["article_id"], llm)
            cursor = await db.execute(
                "SELECT ghost_image_url FROM article_images WHERE article_id = ?",
                (article_ready_to_publish["article_id"],),
            )
            images = await cursor.fetchall()
            for img in images:
                if img["ghost_image_url"]:
                    assert "blog.example.com" in img["ghost_image_url"]

    @pytest.mark.asyncio
    async def test_pipeline_events_audit_trail(self, db, config, article_ready_to_publish):
        p1, p2, p3, p4, _, _ = _ghost_mocks()
        with p1, p2, p3, p4:
            llm = MockLLM()
            await run_publishing(db, config, article_ready_to_publish["article_id"], llm)
            cursor = await db.execute(
                "SELECT * FROM pipeline_events WHERE article_id = ? ORDER BY created_at",
                (article_ready_to_publish["article_id"],),
            )
            events = [dict(r) for r in await cursor.fetchall()]
            transitions = [e for e in events if e["event_type"] == "state_transition"]
            assert len(transitions) >= 2  # READY_TO_PUBLISH → PUBLISHING → PUBLISHED

    @pytest.mark.asyncio
    async def test_crash_recovery_uses_existing_post(self, db, config, article_ready_to_publish):
        """If post already exists on Ghost (crash recovery), don't create a duplicate."""
        existing_post = {"id": "existing-123", "url": "https://blog.example.com/existing/"}
        mock_create = AsyncMock()
        mock_dup = AsyncMock(return_value=existing_post)
        mock_fetch = AsyncMock(return_value=b"\x89PNG")

        with (
            patch("app.pipeline.transitions.t11_publishing.create_ghost_post", mock_create),
            patch("app.pipeline.transitions.t11_publishing.upload_image_to_ghost", AsyncMock(return_value="https://x.com/img.webp")),
            patch("app.pipeline.transitions.t11_publishing.check_duplicate_post", mock_dup),
            patch("app.pipeline.transitions.t11_publishing._fetch_image_bytes", mock_fetch),
        ):
            llm = MockLLM()
            result = await run_publishing(db, config, article_ready_to_publish["article_id"], llm)
            assert result["success"] is True
            # create_ghost_post should NOT have been called
            mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_html_body_has_no_duplicate_title(self, db, config, article_ready_to_publish):
        """The leading H1 must be stripped from article HTML — Ghost renders title separately."""
        p1, p2, p3, p4, mock_create, _ = _ghost_mocks()
        with p1, p2, p3, p4:
            llm = MockLLM()
            await run_publishing(db, config, article_ready_to_publish["article_id"], llm)
            mock_create.assert_called_once()
            # create_ghost_post(ghost_url, ghost_api_key, post_data) — 3 positional args
            post_data = mock_create.call_args[0][2]
            body_html = post_data["html"]
            # The body HTML should NOT contain an <h1> (Ghost renders title from post metadata)
            import re
            inner = re.sub(r'<!--kg-card-(?:begin|end): html-->', '', body_html).strip()
            assert '<h1' not in inner.lower(), f"Body HTML should not contain <h1>: {inner[:300]}"

    @pytest.mark.asyncio
    async def test_html_body_has_no_duplicate_cover_image(self, db, config, article_ready_to_publish):
        """The COVER image must be stripped from article body — Ghost renders it as feature_image."""
        p1, p2, p3, p4, mock_create, _ = _ghost_mocks()
        with p1, p2, p3, p4:
            llm = MockLLM()
            await run_publishing(db, config, article_ready_to_publish["article_id"], llm)
            mock_create.assert_called_once()
            post_data = mock_create.call_args[0][2]
            body_html = post_data["html"]
            feature_image = post_data.get("feature_image", "")
            if feature_image:
                assert feature_image not in body_html, (
                    f"Feature image URL should not appear in body HTML. "
                    f"feature_image={feature_image}, body_html[:300]={body_html[:300]}"
                )

    @pytest.mark.asyncio
    async def test_no_ghost_config_raises(self, db, config):
        """Publishing without Ghost configured should fail with clear error."""
        clear_sent_emails()
        user = await create_user(db, "noconfig@test.com")
        await update_user(db, user["id"], subscription_status="active")
        article_id = await _create_article(db, user["id"], "READY_TO_PUBLISH")
        await db.execute(
            "UPDATE articles SET state = 'READY_TO_PUBLISH' WHERE id = ?",
            (article_id,),
        )
        await db.commit()

        llm = MockLLM()
        with pytest.raises(Exception, match="Ghost not configured"):
            await run_publishing(db, config, article_id, llm)
