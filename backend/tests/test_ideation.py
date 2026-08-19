"""Tests for T1 ideation transition (Task 2.3)."""

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.pipeline.transitions.t1_ideation import run_ideation
from app.llm.mock import MockLLM
from app.services.email import get_sent_emails, clear_sent_emails
from app.utils.ulid import generate_id
from app.utils.time import utc_now
from app.models.seed_batch import create_seed_batch

from tests.conftest import *


@pytest_asyncio.fixture
async def user_with_sub(db, config):
    user = await create_user(db, "ideation@test.com")
    await update_user(db, user["id"], subscription_status="active", ghost_key_valid=1)
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
    return dict(await cursor.fetchone())


@pytest_asyncio.fixture
async def batch_with_seeds(db, user_with_sub):
    """Create a batch with 2 seeds."""
    seeds = [
        {"seed_type": "topic", "content": "AI in healthcare"},
        {"seed_type": "topic", "content": "Python testing"},
    ]
    batch_id, _ = await create_seed_batch(db, user_with_sub["id"], seeds)
    return batch_id


class TestIdeation:
    @pytest.mark.asyncio
    async def test_generates_correct_ideas(self, db, config, user_with_sub, batch_with_seeds):
        clear_sent_emails()
        llm = MockLLM()
        result = await run_ideation(db, config, batch_with_seeds, llm)
        assert result["success"] is True
        # 2 seeds × 3 ideas_per_seed = 6 ideas
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM ideas WHERE batch_id = ?", (batch_with_seeds,)
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 6

    @pytest.mark.asyncio
    async def test_batch_transitions_to_waiting(self, db, config, user_with_sub, batch_with_seeds):
        clear_sent_emails()
        llm = MockLLM()
        await run_ideation(db, config, batch_with_seeds, llm)
        cursor = await db.execute(
            "SELECT status FROM seed_batches WHERE id = ?", (batch_with_seeds,)
        )
        row = await cursor.fetchone()
        assert row["status"] == "waiting_approval"

    @pytest.mark.asyncio
    async def test_no_magic_link_or_email(self, db, config, user_with_sub, batch_with_seeds):
        """Ideation no longer creates CP1 magic links or sends emails (Trello #299)."""
        clear_sent_emails()
        llm = MockLLM()
        await run_ideation(db, config, batch_with_seeds, llm)
        # No magic link should be created
        cursor = await db.execute(
            "SELECT * FROM magic_links WHERE user_id = ? AND purpose = 'checkpoint_1'",
            (user_with_sub["id"],),
        )
        row = await cursor.fetchone()
        assert row is None
        # No email should be sent
        emails = get_sent_emails()
        cp1_emails = [e for e in emails if e.get("purpose") == "checkpoint_1"]
        assert len(cp1_emails) == 0

    @pytest.mark.asyncio
    async def test_pipeline_event_logged(self, db, config, user_with_sub, batch_with_seeds):
        clear_sent_emails()
        llm = MockLLM()
        await run_ideation(db, config, batch_with_seeds, llm)
        cursor = await db.execute(
            "SELECT * FROM pipeline_events WHERE batch_id = ? AND event_type = 'state_transition'",
            (batch_with_seeds,),
        )
        row = await cursor.fetchone()
        assert row is not None
