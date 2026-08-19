"""Full E2E test: seed → ideation → CP1 → outline → draft loop → images → CP2 → publish (Task 2.8)."""

import json

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.models.user import create_user, update_user
from app.models.seed_batch import create_seed_batch
from app.models.usage import get_or_create_current_ledger
from app.pipeline.transitions.t1_ideation import run_ideation
from app.pipeline.transitions.t2_idea_approval import approve_ideas
from app.pipeline.transitions.t3_outlining import run_outlining
from app.pipeline.transitions.t4_drafting import run_drafting
from app.pipeline.transitions.t5_humanizing import run_humanizing
from app.pipeline.transitions.t6_edit_review import run_edit_review
from app.pipeline.transitions.t7_media_assembly import run_media_assembly
from app.pipeline.transitions.t8_to_checkpoint_2 import run_to_checkpoint_2
from app.pipeline.transitions.t11_publishing import run_publishing
from unittest.mock import AsyncMock, patch
from app.pipeline.scheduler import compute_next_publish_slot, get_taken_slots
from app.llm.mock import MockLLM
from app.services.email import get_sent_emails, clear_sent_emails
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *


class TestE2EPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, db, config):
        """Complete end-to-end: seed → ideation → CP1 approve → outline → draft loop → images → CP2 approve → publish."""
        clear_sent_emails()
        llm = MockLLM()

        # === SETUP: Create user with active subscription ===
        user = await create_user(db, "e2e@test.com")
        await update_user(
            db, user["id"],
            subscription_status="active",
            ghost_key_valid=1,
            ghost_url="https://blog.example.com",
            ghost_admin_api_key="abc123def456:aabbccdd00112233445566778899aabbccddeeff00112233445566778899aabb",
            publish_days='["monday","thursday"]',
            publish_time="09:00",
            publish_timezone="UTC",
        )
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user["id"],))
        user = dict(await cursor.fetchone())
        await get_or_create_current_ledger(db, user["id"])

        # === STEP 1: Submit seeds ===
        seeds = [{"seed_type": "topic", "content": "AI in sustainable packaging"}]
        batch_id, _ = await create_seed_batch(db, user["id"], seeds)

        cursor = await db.execute("SELECT status FROM seed_batches WHERE id = ?", (batch_id,))
        assert (await cursor.fetchone())["status"] == "pending_ideation"

        # === STEP 2: T1 — Ideation ===
        result = await run_ideation(db, config, batch_id, llm)
        assert result["success"] is True
        assert result["ideas_count"] == 3  # 1 seed × 3 ideas

        cursor = await db.execute("SELECT status FROM seed_batches WHERE id = ?", (batch_id,))
        assert (await cursor.fetchone())["status"] == "waiting_approval"

        # CP1 email no longer sent (Trello #299) — user is on dashboard

        # === STEP 3: T2 — CP1 Approve (1 idea) ===
        cursor = await db.execute("SELECT id FROM ideas WHERE batch_id = ? LIMIT 1", (batch_id,))
        idea_id = (await cursor.fetchone())["id"]

        result = await approve_ideas(db, user["id"], batch_id, [{"id": idea_id}])
        assert result["articles_created"] == 1

        cursor = await db.execute("SELECT id, state FROM articles WHERE user_id = ?", (user["id"],))
        article = dict(await cursor.fetchone())
        article_id = article["id"]
        assert article["state"] == "OUTLINING"

        # === STEP 4: T3 — Outline ===
        result = await run_outlining(db, config, article_id, llm)
        assert result["success"] is True

        cursor = await db.execute("SELECT state, outline_json, seo_meta FROM articles WHERE id = ?", (article_id,))
        row = await cursor.fetchone()
        assert row["state"] == "DRAFTING"
        assert row["outline_json"] is not None
        assert row["seo_meta"] is not None

        # === STEP 5: T4/T5/T6 — Draft Loop (iteration 1: reject) ===
        await run_drafting(db, config, article_id, llm)
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "HUMANIZING"

        await run_humanizing(db, config, article_id, llm)
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "EDIT_REVIEW"

        result = await run_edit_review(db, config, article_id, llm)
        assert result["verdict"] == "revision_needed"  # Iteration 1: score 6
        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "DRAFTING"

        # === STEP 6: Draft Loop (iteration 2: approve) ===
        await run_drafting(db, config, article_id, llm)
        await run_humanizing(db, config, article_id, llm)
        result = await run_edit_review(db, config, article_id, llm)
        assert result["verdict"] == "approved"  # Iteration 2: score 8

        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "MEDIA_ASSEMBLY"

        # === STEP 7: T7 — Media Assembly ===
        result = await run_media_assembly(db, config, article_id, llm)
        assert result["success"] is True
        assert result["images_count"] >= 2

        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "WAITING_CHECKPOINT_2"

        # === STEP 8: T8 — Setup CP2 ===
        await run_to_checkpoint_2(db, config, article_id)

        # Verify CP2 email
        emails = get_sent_emails()
        cp2_emails = [e for e in emails if e["purpose"] in ("checkpoint_2", "article_review")]
        assert len(cp2_emails) >= 1

        # Verify review row
        cursor = await db.execute(
            "SELECT * FROM article_reviews WHERE article_id = ?", (article_id,)
        )
        review = await cursor.fetchone()
        assert review["status"] == "pending"

        # === STEP 9: CP2 Approve ===
        now = utc_now()
        await db.execute(
            "UPDATE article_reviews SET status = 'approved', reviewed_at = ? WHERE article_id = ? AND status = 'pending'",
            (now, article_id),
        )
        taken = await get_taken_slots(db, user["id"])
        slot = compute_next_publish_slot(
            json.loads(user["publish_days"]),
            user["publish_time"],
            user["publish_timezone"],
            taken_slots=taken,
        )
        await db.execute(
            "UPDATE articles SET state = 'READY_TO_PUBLISH', scheduled_publish_at = ?, updated_at = ? WHERE id = ?",
            (slot, now, article_id),
        )
        await db.commit()

        cursor = await db.execute("SELECT state FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["state"] == "READY_TO_PUBLISH"

        # === STEP 10: T11 — Publish (Ghost API mocked) ===
        clear_sent_emails()
        with (
            patch("app.pipeline.transitions.t11_publishing.create_ghost_post", AsyncMock(return_value={"id": "ghost-e2e-001", "url": "https://blog.example.com/test/", "slug": "test"})),
            patch("app.pipeline.transitions.t11_publishing.upload_image_to_ghost", AsyncMock(return_value="https://blog.example.com/content/images/test.webp")),
            patch("app.pipeline.transitions.t11_publishing.check_duplicate_post", AsyncMock(return_value=None)),
            patch("app.pipeline.transitions.t11_publishing._fetch_image_bytes", AsyncMock(return_value=b"\x89PNG")),
        ):
            result = await run_publishing(db, config, article_id, llm)
            assert result["success"] is True
            assert "ghost_post_url" in result

        cursor = await db.execute(
            "SELECT state, ghost_post_id, ghost_post_url, published_at FROM articles WHERE id = ?",
            (article_id,),
        )
        final = dict(await cursor.fetchone())
        assert final["state"] == "PUBLISHED"
        assert final["ghost_post_id"] is not None
        assert final["ghost_post_url"] is not None
        assert final["published_at"] is not None

        # Verify notification email
        emails = get_sent_emails()
        notif = [e for e in emails if e.get("purpose") == "publish_notification"]
        assert len(notif) >= 1

        # === VERIFY AUDIT TRAIL ===
        cursor = await db.execute(
            "SELECT * FROM pipeline_events WHERE article_id = ? ORDER BY created_at",
            (article_id,),
        )
        events = [dict(r) for r in await cursor.fetchall()]
        transitions = [e for e in events if e["event_type"] == "state_transition"]
        states_visited = [e["to_state"] for e in transitions]

        assert "DRAFTING" in states_visited
        assert "HUMANIZING" in states_visited
        assert "EDIT_REVIEW" in states_visited
        assert "MEDIA_ASSEMBLY" in states_visited
        assert "PUBLISHED" in states_visited

        # Verify draft iterations
        cursor = await db.execute(
            "SELECT * FROM draft_iterations WHERE article_id = ? ORDER BY iteration_number",
            (article_id,),
        )
        drafts = [dict(r) for r in await cursor.fetchall()]
        assert len(drafts) == 2  # 2 iterations

        # Verify images
        cursor = await db.execute(
            "SELECT * FROM article_images WHERE article_id = ?",
            (article_id,),
        )
        images = await cursor.fetchall()
        assert len(images) >= 2

        print(f"\n✅ E2E COMPLETE: {len(transitions)} state transitions, {len(drafts)} draft iterations, {len(images)} images")


class TestSequentialProcessing:
    @pytest.mark.asyncio
    async def test_one_article_per_user(self, db, config):
        """Only one article processes at a time per user."""
        from app.pipeline.worker import find_processable_articles

        user = await create_user(db, "seq@test.com")
        await update_user(db, user["id"], subscription_status="active")
        from tests.test_locking import _create_article
        await _create_article(db, user["id"], "OUTLINING")
        await _create_article(db, user["id"], "DRAFTING")

        articles = await find_processable_articles(db, max_concurrent=10)
        user_articles = [a for a in articles if a["user_id"] == user["id"]]
        assert len(user_articles) == 1

    @pytest.mark.asyncio
    async def test_different_users_parallel(self, db, config):
        from app.pipeline.worker import find_processable_articles

        user1 = await create_user(db, "par1@test.com")
        user2 = await create_user(db, "par2@test.com")
        await update_user(db, user1["id"], subscription_status="active")
        await update_user(db, user2["id"], subscription_status="active")
        from tests.test_locking import _create_article
        await _create_article(db, user1["id"], "OUTLINING")
        await _create_article(db, user2["id"], "OUTLINING")

        articles = await find_processable_articles(db, max_concurrent=10)
        assert len(articles) == 2


class TestErrorRecovery:
    @pytest.mark.asyncio
    async def test_lock_released_on_exception(self, db, config):
        """Lock is released even if processing fails."""
        from app.pipeline.locking import acquire_lock, release_lock

        user = await create_user(db, "err@test.com")
        await update_user(db, user["id"], subscription_status="active")
        from tests.test_locking import _create_article
        article_id = await _create_article(db, user["id"], "OUTLINING")

        await acquire_lock(db, article_id, "worker-01")

        # Simulate error handling: always release in finally
        try:
            raise Exception("Simulated error")
        except Exception:
            pass
        finally:
            await release_lock(db, article_id, "worker-01")

        cursor = await db.execute("SELECT locked_by FROM articles WHERE id = ?", (article_id,))
        assert (await cursor.fetchone())["locked_by"] is None

    @pytest.mark.asyncio
    async def test_failed_state_preserves_data(self, db, config):
        """Failed transition doesn't corrupt article data."""
        user = await create_user(db, "fail@test.com")
        await update_user(db, user["id"], subscription_status="active")
        from tests.test_locking import _create_article
        article_id = await _create_article(db, user["id"], "OUTLINING")

        # Run outline
        llm = MockLLM()
        await run_outlining(db, config, article_id, llm)

        # Simulate: set iterations to cap and try drafting
        await db.execute(
            "UPDATE articles SET lifetime_draft_iterations = 5 WHERE id = ?", (article_id,)
        )
        await db.commit()

        result = await run_drafting(db, config, article_id, llm)
        assert result["success"] is False

        # Article should be FAILED but outline data preserved
        cursor = await db.execute(
            "SELECT state, outline_json FROM articles WHERE id = ?", (article_id,)
        )
        row = await cursor.fetchone()
        assert row["state"] == "FAILED"
        assert row["outline_json"] is not None  # Data preserved
