"""Tests for T4-T6 drafting loop (Task 2.5)."""

import json

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.pipeline.transitions.t3_outlining import run_outlining
from app.pipeline.transitions.t4_drafting import run_drafting
from app.pipeline.transitions.t5_humanizing import run_humanizing
from app.pipeline.transitions.t6_edit_review import run_edit_review
from app.pipeline.state_machine import dispatch_article
from app.llm.mock import MockLLM
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *
from tests.test_locking import _create_article


@pytest_asyncio.fixture
async def article_with_outline(db, config):
    """Article that has been through outlining (ready for DRAFTING)."""
    user = await create_user(db, "draft@test.com")
    await update_user(db, user["id"], subscription_status="active", ghost_key_valid=1)
    article_id = await _create_article(db, user["id"], "OUTLINING")
    llm = MockLLM()
    await run_outlining(db, config, article_id, llm)
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
    user = dict(await cursor.fetchone())
    return {"user": user, "article_id": article_id}


class TestDrafting:
    @pytest.mark.asyncio
    async def test_creates_draft_iteration(self, db, config, article_with_outline):
        llm = MockLLM()
        result = await run_drafting(db, config, article_with_outline["article_id"], llm)
        assert result["success"] is True
        cursor = await db.execute(
            "SELECT * FROM draft_iterations WHERE article_id = ?",
            (article_with_outline["article_id"],),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["iteration_number"] == 1
        assert row["raw_draft_md"] is not None
        assert len(row["raw_draft_md"]) > 100

    @pytest.mark.asyncio
    async def test_transitions_to_humanizing(self, db, config, article_with_outline):
        llm = MockLLM()
        await run_drafting(db, config, article_with_outline["article_id"], llm)
        cursor = await db.execute(
            "SELECT state, lifetime_draft_iterations FROM articles WHERE id = ?",
            (article_with_outline["article_id"],),
        )
        row = await cursor.fetchone()
        assert row["state"] == "HUMANIZING"
        assert row["lifetime_draft_iterations"] == 1


class TestHumanizing:
    @pytest.mark.asyncio
    async def test_humanizes_draft(self, db, config, article_with_outline):
        llm = MockLLM()
        await run_drafting(db, config, article_with_outline["article_id"], llm)
        result = await run_humanizing(db, config, article_with_outline["article_id"], llm)
        assert result["success"] is True
        cursor = await db.execute(
            "SELECT humanized_draft_md FROM draft_iterations WHERE article_id = ? ORDER BY iteration_number DESC LIMIT 1",
            (article_with_outline["article_id"],),
        )
        row = await cursor.fetchone()
        assert row["humanized_draft_md"] is not None

    @pytest.mark.asyncio
    async def test_transitions_to_edit_review(self, db, config, article_with_outline):
        llm = MockLLM()
        await run_drafting(db, config, article_with_outline["article_id"], llm)
        await run_humanizing(db, config, article_with_outline["article_id"], llm)
        cursor = await db.execute(
            "SELECT state FROM articles WHERE id = ?",
            (article_with_outline["article_id"],),
        )
        assert (await cursor.fetchone())["state"] == "EDIT_REVIEW"


class TestEditReview:
    @pytest.mark.asyncio
    async def test_iteration_1_loops_back(self, db, config, article_with_outline):
        """First iteration: score 6 → revision_needed → back to DRAFTING."""
        llm = MockLLM()
        await run_drafting(db, config, article_with_outline["article_id"], llm)
        await run_humanizing(db, config, article_with_outline["article_id"], llm)
        result = await run_edit_review(db, config, article_with_outline["article_id"], llm)
        assert result["success"] is True
        assert result["verdict"] == "revision_needed"
        cursor = await db.execute(
            "SELECT state FROM articles WHERE id = ?",
            (article_with_outline["article_id"],),
        )
        assert (await cursor.fetchone())["state"] == "DRAFTING"

    @pytest.mark.asyncio
    async def test_full_loop_approve_on_iteration_2(self, db, config, article_with_outline):
        """Full draft loop: reject on iter 1, approve on iter 2 (score 8)."""
        llm = MockLLM()
        aid = article_with_outline["article_id"]

        # Iteration 1: draft → humanize → review (score 6, reject)
        await run_drafting(db, config, aid, llm)
        await run_humanizing(db, config, aid, llm)
        await run_edit_review(db, config, aid, llm)

        # Iteration 2: draft → humanize → review (score 8, approve)
        await run_drafting(db, config, aid, llm)
        await run_humanizing(db, config, aid, llm)
        result = await run_edit_review(db, config, aid, llm)

        assert result["verdict"] == "approved"
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (aid,))
        assert (await cursor.fetchone())["state"] == "MEDIA_ASSEMBLY"

    @pytest.mark.asyncio
    async def test_score_7_override(self, db, config, article_with_outline):
        """Score >= 7 override: iteration 3 has score 7, verdict revision_needed → software approves."""
        llm = MockLLM()
        aid = article_with_outline["article_id"]

        # Run 3 full iterations
        for _ in range(3):
            await run_drafting(db, config, aid, llm)
            await run_humanizing(db, config, aid, llm)
            result = await run_edit_review(db, config, aid, llm)

        # Iteration 3: score 7, verdict "revision_needed" → overridden to approved
        assert result["verdict"] == "approved"  # Software override
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (aid,))
        assert (await cursor.fetchone())["state"] == "MEDIA_ASSEMBLY"

    @pytest.mark.asyncio
    async def test_force_advance_at_cap(self, db, config):
        """At lifetime_draft_iterations cap (5), force-advance to MEDIA_ASSEMBLY."""
        user = await create_user(db, "cap@test.com")
        await update_user(db, user["id"], subscription_status="active", ghost_key_valid=1)
        article_id = await _create_article(db, user["id"], "OUTLINING")
        llm = MockLLM()
        await run_outlining(db, config, article_id, llm)

        # Set lifetime_draft_iterations to 4 (one more = cap of 5)
        await db.execute(
            "UPDATE articles SET lifetime_draft_iterations = 4 WHERE id = ?", (article_id,)
        )
        await db.commit()

        # This will be iteration 5 (hits cap after drafting)
        await run_drafting(db, config, article_id, llm)
        await run_humanizing(db, config, article_id, llm)
        result = await run_edit_review(db, config, article_id, llm)

        # Should be force-advanced even if critique says revision_needed
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "MEDIA_ASSEMBLY"


class TestStateMachineDispatcher:
    @pytest.mark.asyncio
    async def test_dispatches_outlining(self, db, config):
        user = await create_user(db, "dispatch@test.com")
        await update_user(db, user["id"], subscription_status="active", ghost_key_valid=1)
        article_id = await _create_article(db, user["id"], "OUTLINING")
        llm = MockLLM()
        result = await dispatch_article(db, config, article_id, llm)
        assert result["success"] is True
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "DRAFTING"

    @pytest.mark.asyncio
    async def test_dispatches_drafting(self, db, config):
        user = await create_user(db, "dispatch2@test.com")
        await update_user(db, user["id"], subscription_status="active", ghost_key_valid=1)
        article_id = await _create_article(db, user["id"], "OUTLINING")
        llm = MockLLM()
        await run_outlining(db, config, article_id, llm)
        result = await dispatch_article(db, config, article_id, llm)
        assert result["success"] is True
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "HUMANIZING"
