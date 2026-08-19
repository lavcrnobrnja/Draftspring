"""Tests for Checkpoint 2 + Revision (Task 2.7)."""

import json

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.config import Config
from app.database import get_connection, run_migrations
from app.main import create_app
from app.models.user import create_user, update_user
from app.middleware.auth_middleware import create_session
from app.pipeline.transitions.t3_outlining import run_outlining
from app.pipeline.transitions.t4_drafting import run_drafting
from app.pipeline.transitions.t5_humanizing import run_humanizing
from app.pipeline.transitions.t6_edit_review import run_edit_review
from app.pipeline.transitions.t7_media_assembly import run_media_assembly
from app.pipeline.transitions.t8_to_checkpoint_2 import run_to_checkpoint_2
from app.llm.mock import MockLLM
from app.services.email import get_sent_emails, clear_sent_emails
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *
from tests.test_locking import _create_article


@pytest_asyncio.fixture
async def article_at_cp2(db, config):
    """Article at WAITING_CHECKPOINT_2 with review row and magic link."""
    clear_sent_emails()
    user = await create_user(db, "cp2@test.com")
    await update_user(
        db, user["id"],
        subscription_status="active",
        ghost_key_valid=1,
        ghost_url="https://blog.example.com",
    )
    article_id = await _create_article(db, user["id"], "OUTLINING")
    llm = MockLLM()

    await run_outlining(db, config, article_id, llm)
    # 2 iterations to get to MEDIA_ASSEMBLY
    await run_drafting(db, config, article_id, llm)
    await run_humanizing(db, config, article_id, llm)
    await run_edit_review(db, config, article_id, llm)
    await run_drafting(db, config, article_id, llm)
    await run_humanizing(db, config, article_id, llm)
    await run_edit_review(db, config, article_id, llm)
    # Media assembly transitions to WAITING_CHECKPOINT_2 and calls T8 internally
    # (T8 creates review row, magic link, sends email)
    await run_media_assembly(db, config, article_id, llm)

    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
    user = dict(await cursor.fetchone())

    # Create sessions
    cp2_session = await create_session(db, user["id"], "checkpoint_2", scope_ref=article_id)
    full_session = await create_session(db, user["id"], "full")

    return {
        "user": user,
        "article_id": article_id,
        "cp2_session": cp2_session,
        "full_session": full_session,
    }


class TestT8ToCheckpoint2:
    @pytest.mark.asyncio
    async def test_review_row_created(self, db, config, article_at_cp2):
        cursor = await db.execute(
            "SELECT * FROM article_reviews WHERE article_id = ?",
            (article_at_cp2["article_id"],),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "pending"
        assert row["review_number"] == 1

    @pytest.mark.asyncio
    async def test_magic_link_created(self, db, config, article_at_cp2):
        cursor = await db.execute(
            "SELECT * FROM magic_links WHERE user_id = ? AND purpose = 'checkpoint_2'",
            (article_at_cp2["user"]["id"],),
        )
        row = await cursor.fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_email_sent(self, db, config, article_at_cp2):
        emails = get_sent_emails()
        cp2_emails = [e for e in emails if e["purpose"] in ("checkpoint_2", "article_review")]
        assert len(cp2_emails) >= 1


class TestGetArticlePreview:
    @pytest.mark.asyncio
    async def test_returns_preview(self, db, config, article_at_cp2):
        app = create_app(config)
        client = TestClient(app)
        response = client.get(
            f"/api/checkpoints/article/{article_at_cp2['article_id']}",
            cookies={"session_id": article_at_cp2["cp2_session"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert "draft_html" in data
        assert "images" in data
        assert "seo" in data
        assert "review_history" in data
        assert "budget_remaining" in data

    @pytest.mark.asyncio
    async def test_wrong_article_403(self, db, config, article_at_cp2):
        """Scoped session for wrong article → 403."""
        # Create another article
        other_article = await _create_article(db, article_at_cp2["user"]["id"], "OUTLINING")
        app = create_app(config)
        client = TestClient(app)
        response = client.get(
            f"/api/checkpoints/article/{other_article}",
            cookies={"session_id": article_at_cp2["cp2_session"]},
        )
        assert response.status_code == 403


class TestApproveArticle:
    @pytest.mark.asyncio
    async def test_approve_transitions(self, db, config, article_at_cp2):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/checkpoints/article/approve",
            json={"article_id": article_at_cp2["article_id"]},
            cookies={"session_id": article_at_cp2["cp2_session"]},
        )
        assert response.status_code == 200
        cursor = await db.execute(
            "SELECT state, scheduled_publish_at FROM articles WHERE id = ?",
            (article_at_cp2["article_id"],),
        )
        row = await cursor.fetchone()
        assert row["state"] == "READY_TO_PUBLISH"
        assert row["scheduled_publish_at"] is not None


class TestReviseArticle:
    @pytest.mark.asyncio
    async def test_revise_transitions(self, db, config, article_at_cp2):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/checkpoints/article/revise",
            json={
                "article_id": article_at_cp2["article_id"],
                "revision_notes": "Please make the introduction more engaging and add more data points.",
            },
            cookies={"session_id": article_at_cp2["cp2_session"]},
        )
        assert response.status_code == 200
        cursor = await db.execute(
            "SELECT state FROM articles WHERE id = ?",
            (article_at_cp2["article_id"],),
        )
        assert (await cursor.fetchone())["state"] == "REVISION"

    @pytest.mark.asyncio
    async def test_short_notes_rejected(self, db, config, article_at_cp2):
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/checkpoints/article/revise",
            json={
                "article_id": article_at_cp2["article_id"],
                "revision_notes": "Fix it",
            },
            cookies={"session_id": article_at_cp2["cp2_session"]},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_budget_exhausted(self, db, config, article_at_cp2):
        """Revision rejected when draft iterations at cap."""
        await db.execute(
            "UPDATE articles SET lifetime_draft_iterations = 5 WHERE id = ?",
            (article_at_cp2["article_id"],),
        )
        await db.commit()
        app = create_app(config)
        client = TestClient(app)
        response = client.post(
            "/api/checkpoints/article/revise",
            json={
                "article_id": article_at_cp2["article_id"],
                "revision_notes": "Please revise this article with more detail and examples.",
            },
            cookies={"session_id": article_at_cp2["cp2_session"]},
        )
        assert response.status_code == 400


class TestRevisionFeedsBack:
    @pytest.mark.asyncio
    async def test_revision_to_drafting(self, db, config, article_at_cp2):
        """Revision feeds back into drafting loop."""
        # Revise
        await db.execute(
            "UPDATE articles SET state = 'REVISION' WHERE id = ?",
            (article_at_cp2["article_id"],),
        )
        # Create a review with revision notes
        review_id = generate_id()
        now = utc_now()
        await db.execute(
            """INSERT INTO article_reviews (id, article_id, review_number, status, revision_notes, created_at)
               VALUES (?, ?, 2, 'revision_requested', 'Add more data and examples to support the arguments.', ?)""",
            (review_id, article_at_cp2["article_id"], now),
        )
        await db.commit()

        from app.pipeline.transitions.t10_revision import run_revision
        result = await run_revision(db, config, article_at_cp2["article_id"], MockLLM())
        assert result["success"] is True
        cursor = await db.execute(
            "SELECT state FROM articles WHERE id = ?",
            (article_at_cp2["article_id"],),
        )
        assert (await cursor.fetchone())["state"] == "DRAFTING"

    @pytest.mark.asyncio
    async def test_text_only_revision_skips_media_assembly(self, db, config, article_at_cp2):
        """Revision with text-only notes skips MEDIA_ASSEMBLY, preserves images."""
        article_id = article_at_cp2["article_id"]

        # Put article back in REVISION state with text-only notes
        await db.execute("UPDATE articles SET state = 'REVISION' WHERE id = ?", (article_id,))
        review_id = generate_id()
        now = utc_now()
        await db.execute(
            """INSERT INTO article_reviews (id, article_id, review_number, status, revision_notes, created_at)
               VALUES (?, ?, 2, 'revision_requested', 'Add more data and examples to support the arguments.', ?)""",
            (review_id, article_id, now),
        )
        await db.commit()

        # Run through revision → drafting → humanizing → edit_review
        from app.pipeline.transitions.t10_revision import run_revision
        await run_revision(db, config, article_id, MockLLM())
        await run_drafting(db, config, article_id, MockLLM())
        await run_humanizing(db, config, article_id, MockLLM())
        await run_edit_review(db, config, article_id, MockLLM())

        # Should skip MEDIA_ASSEMBLY and go to WAITING_CHECKPOINT_2
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "WAITING_CHECKPOINT_2"

    @pytest.mark.asyncio
    async def test_image_revision_routes_through_media_assembly(self, db, config, article_at_cp2):
        """Revision with image-related notes routes through MEDIA_ASSEMBLY."""
        article_id = article_at_cp2["article_id"]

        # Put article back in REVISION state with image-related notes
        await db.execute("UPDATE articles SET state = 'REVISION' WHERE id = ?", (article_id,))
        review_id = generate_id()
        now = utc_now()
        await db.execute(
            """INSERT INTO article_reviews (id, article_id, review_number, status, revision_notes, created_at)
               VALUES (?, ?, 2, 'revision_requested', 'The article is great but the images suck. Please change the images to photographs.', ?)""",
            (review_id, article_id, now),
        )
        await db.commit()

        # Run through revision → drafting → humanizing → edit_review
        from app.pipeline.transitions.t10_revision import run_revision
        await run_revision(db, config, article_id, MockLLM())
        await run_drafting(db, config, article_id, MockLLM())
        await run_humanizing(db, config, article_id, MockLLM())
        await run_edit_review(db, config, article_id, MockLLM())

        # Should go to MEDIA_ASSEMBLY (not skip to WAITING_CHECKPOINT_2)
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "MEDIA_ASSEMBLY"
