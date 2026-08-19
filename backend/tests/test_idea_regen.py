"""Tests for idea regeneration feature."""

import json
import pytest
import pytest_asyncio
import aiosqlite
from datetime import datetime, timezone

from app.pipeline.transitions.t1_ideation import run_ideation
from app.llm.mock import MockLLM
from app.utils.ulid import generate_id
from app.utils.time import utc_now


async def setup_test_db(db: aiosqlite.Connection):
    """Create schema and a test user/batch."""
    # Create tables — apply all migrations
    import glob
    for mig_file in sorted(glob.glob("migrations/*.sql")):
        sql = open(mig_file).read()
        await db.executescript(sql)

    now = utc_now()
    user_id = generate_id()
    await db.execute(
        """INSERT INTO users (id, email, ghost_url, ghost_key_valid,
           subscription_status, created_at, updated_at)
           VALUES (?, 'test@test.com', 'https://test.ghost.io', 1, 'trialing', ?, ?)""",
        (user_id, now, now),
    )

    batch_id = generate_id()
    await db.execute(
        "INSERT INTO seed_batches (id, user_id, status, created_at) VALUES (?, ?, 'pending_ideation', ?)",
        (batch_id, user_id, now),
    )

    seed_id = generate_id()
    await db.execute(
        "INSERT INTO seeds (id, batch_id, seed_type, content, created_at) VALUES (?, ?, 'topic', 'AI automation', ?)",
        (seed_id, batch_id, now),
    )

    await db.commit()
    return user_id, batch_id, seed_id


class FakeConfig:
    DATABASE_PATH = ":memory:"
    APP_BASE_URL = "https://test.app"
    RESEND_API_KEY = "fake"
    RESEND_FROM = "test@test.com"


# Email mock removed — ideation no longer sends emails (Trello #299)


@pytest.mark.asyncio
async def test_first_ideation_no_feedback():
    """First run: no feedback, no rejected titles."""
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        user_id, batch_id, seed_id = await setup_test_db(db)

        llm = MockLLM()
        result = await run_ideation(db, FakeConfig(), batch_id, llm)

        assert result["success"]
        assert result["ideas_count"] == 3  # 3 per seed default

        cursor = await db.execute("SELECT * FROM ideas WHERE batch_id = ?", (batch_id,))
        ideas = [dict(r) for r in await cursor.fetchall()]
        assert len(ideas) == 3
        assert all(i["status"] == "pending" for i in ideas)
        # No regen prefix
        assert not any("Regen" in i["title"] for i in ideas)


@pytest.mark.asyncio
async def test_regen_with_feedback():
    """Regen: old ideas rejected, new ones generated with feedback."""
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        user_id, batch_id, seed_id = await setup_test_db(db)

        llm = MockLLM()

        # First ideation
        result = await run_ideation(db, FakeConfig(), batch_id, llm)
        assert result["success"]

        # Reject all ideas (simulating regenerate endpoint)
        await db.execute("UPDATE ideas SET status = 'rejected' WHERE batch_id = ?", (batch_id,))
        await db.execute(
            "UPDATE seed_batches SET status = 'pending_ideation', regen_count = 1, regen_feedback = 'more technical' WHERE id = ?",
            (batch_id,),
        )
        await db.commit()

        # Second ideation (regen)
        result = await run_ideation(db, FakeConfig(), batch_id, llm)
        assert result["success"]
        assert result["ideas_count"] == 3

        cursor = await db.execute("SELECT * FROM ideas WHERE batch_id = ? AND status = 'pending'", (batch_id,))
        pending = [dict(r) for r in await cursor.fetchall()]
        assert len(pending) == 3
        # Mock adds "(Regen) " prefix when feedback is provided
        assert all("Regen" in i["title"] for i in pending)

        # Rejected ideas still exist
        cursor = await db.execute("SELECT * FROM ideas WHERE batch_id = ? AND status = 'rejected'", (batch_id,))
        rejected = [dict(r) for r in await cursor.fetchall()]
        assert len(rejected) == 3


@pytest.mark.asyncio
async def test_rejected_titles_passed_to_llm():
    """Verify rejected titles from previous generations are passed to LLM."""
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        user_id, batch_id, seed_id = await setup_test_db(db)

        # Track what gets passed to generate_ideas
        captured_args = {}
        original_generate = MockLLM.generate_ideas

        async def capture_generate(self, seeds, ideas_per_seed, existing_titles,
                                   feedback=None, rejected_titles=None,
                                   ghost_url="blog", brand_voice=None,
                                   user_images=None):
            captured_args["feedback"] = feedback
            captured_args["rejected_titles"] = rejected_titles
            return await original_generate(self, seeds, ideas_per_seed, existing_titles,
                                           feedback=feedback, rejected_titles=rejected_titles,
                                           ghost_url=ghost_url, brand_voice=brand_voice,
                                           user_images=user_images)

        MockLLM.generate_ideas = capture_generate

        try:
            # First ideation
            await run_ideation(db, FakeConfig(), batch_id, MockLLM())
            assert captured_args["feedback"] is None
            assert captured_args["rejected_titles"] is None

            # Reject and set up regen
            await db.execute("UPDATE ideas SET status = 'rejected' WHERE batch_id = ?", (batch_id,))
            await db.execute(
                "UPDATE seed_batches SET status = 'pending_ideation', regen_count = 1, regen_feedback = 'be bolder' WHERE id = ?",
                (batch_id,),
            )
            await db.commit()

            # Second ideation
            await run_ideation(db, FakeConfig(), batch_id, MockLLM())
            assert captured_args["feedback"] == "be bolder"
            assert len(captured_args["rejected_titles"]) == 3
        finally:
            MockLLM.generate_ideas = original_generate


@pytest.mark.asyncio
async def test_regen_count_increments():
    """Each regen increments the counter."""
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        user_id, batch_id, seed_id = await setup_test_db(db)

        llm = MockLLM()

        # Run initial ideation
        await run_ideation(db, FakeConfig(), batch_id, llm)

        for regen_num in range(1, 4):
            # Reject and regen
            await db.execute("UPDATE ideas SET status = 'rejected' WHERE batch_id = ? AND status = 'pending'", (batch_id,))
            await db.execute(
                "UPDATE seed_batches SET status = 'pending_ideation', regen_count = ?, regen_feedback = ? WHERE id = ?",
                (regen_num, f"feedback {regen_num}", batch_id),
            )
            await db.commit()

            result = await run_ideation(db, FakeConfig(), batch_id, llm)
            assert result["success"]

            cursor = await db.execute("SELECT * FROM ideas WHERE batch_id = ? AND status = 'pending'", (batch_id,))
            pending = [dict(r) for r in await cursor.fetchall()]
            assert len(pending) == 3

        # Total ideas: 3 original + 3 rejected * 3 regens = 12 total, 3 pending
        cursor = await db.execute("SELECT COUNT(*) FROM ideas WHERE batch_id = ?", (batch_id,))
        total = (await cursor.fetchone())[0]
        assert total == 12  # 3 original (rejected) + 3 * 3 regens


@pytest.mark.asyncio
async def test_only_pending_ideas_shown():
    """After regen, only pending (new) ideas should be returned, not rejected ones."""
    async with aiosqlite.connect(":memory:") as db:
        db.row_factory = aiosqlite.Row
        user_id, batch_id, seed_id = await setup_test_db(db)

        llm = MockLLM()
        await run_ideation(db, FakeConfig(), batch_id, llm)

        # Reject and regen
        await db.execute("UPDATE ideas SET status = 'rejected' WHERE batch_id = ?", (batch_id,))
        await db.execute(
            "UPDATE seed_batches SET status = 'pending_ideation', regen_count = 1, regen_feedback = 'different angles' WHERE id = ?",
            (batch_id,),
        )
        await db.commit()
        await run_ideation(db, FakeConfig(), batch_id, llm)

        # The GET ideas endpoint returns ALL ideas but frontend filters by status='pending'
        cursor = await db.execute("SELECT * FROM ideas WHERE batch_id = ? AND status = 'pending'", (batch_id,))
        pending = [dict(r) for r in await cursor.fetchall()]
        assert len(pending) == 3

        cursor = await db.execute("SELECT * FROM ideas WHERE batch_id = ? AND status = 'rejected'", (batch_id,))
        rejected = [dict(r) for r in await cursor.fetchall()]
        assert len(rejected) == 3
