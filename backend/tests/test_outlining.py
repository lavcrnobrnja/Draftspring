"""Tests for T3 outlining (Task 2.5)."""

import json

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.pipeline.transitions.t3_outlining import run_outlining
from app.llm.mock import MockLLM
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *
from tests.test_locking import _create_article


@pytest_asyncio.fixture
async def article_in_outlining(db):
    user = await create_user(db, "outline@test.com")
    await update_user(db, user["id"], subscription_status="active", ghost_key_valid=1)
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
    user = dict(await cursor.fetchone())
    article_id = await _create_article(db, user["id"], "OUTLINING")
    return {"user": user, "article_id": article_id}


class TestOutlining:
    @pytest.mark.asyncio
    async def test_generates_outline(self, db, config, article_in_outlining):
        llm = MockLLM()
        result = await run_outlining(db, config, article_in_outlining["article_id"], llm)
        assert result["success"] is True
        # Check outline stored
        cursor = await db.execute(
            "SELECT outline_json, seo_meta FROM articles WHERE id = ?",
            (article_in_outlining["article_id"],),
        )
        row = await cursor.fetchone()
        outline = json.loads(row["outline_json"])
        assert len(outline["sections"]) == 5
        seo = json.loads(row["seo_meta"])
        assert "focus_keyword" in seo

    @pytest.mark.asyncio
    async def test_transitions_to_drafting(self, db, config, article_in_outlining):
        llm = MockLLM()
        await run_outlining(db, config, article_in_outlining["article_id"], llm)
        cursor = await db.execute(
            "SELECT state FROM articles WHERE id = ?",
            (article_in_outlining["article_id"],),
        )
        assert (await cursor.fetchone())["state"] == "DRAFTING"

    @pytest.mark.asyncio
    async def test_pipeline_event_logged(self, db, config, article_in_outlining):
        llm = MockLLM()
        await run_outlining(db, config, article_in_outlining["article_id"], llm)
        cursor = await db.execute(
            "SELECT * FROM pipeline_events WHERE article_id = ? AND event_type = 'state_transition'",
            (article_in_outlining["article_id"],),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["from_state"] == "OUTLINING"
        assert row["to_state"] == "DRAFTING"

    @pytest.mark.asyncio
    async def test_stores_visible_tags(self, db, config, article_in_outlining):
        llm = MockLLM()
        await run_outlining(db, config, article_in_outlining["article_id"], llm)
        cursor = await db.execute(
            "SELECT visible_tags FROM articles WHERE id = ?",
            (article_in_outlining["article_id"],),
        )
        row = await cursor.fetchone()
        assert row["visible_tags"] is not None
        tags = json.loads(row["visible_tags"])
        assert len(tags) >= 1
