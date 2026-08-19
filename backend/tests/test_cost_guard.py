"""Tests for cost guard (Task 2.2)."""

import pytest
import pytest_asyncio

from app.database import get_connection, run_migrations
from app.models.user import create_user
from app.pipeline.cost_guard import check_cost_ceiling, record_llm_cost
from app.utils.ulid import generate_id
from app.utils.time import utc_now

from tests.conftest import *
from tests.test_locking import _create_article


class TestCheckCostCeiling:
    @pytest.mark.asyncio
    async def test_under_ceiling(self, db, config):
        user = await create_user(db, "cost1@test.com")
        article_id = await _create_article(db, user["id"])
        result = await check_cost_ceiling(db, article_id, config.PER_ARTICLE_COST_CEILING_CENTS)
        assert result["under_ceiling"] is True
        assert result["total_cost_cents"] == 0

    @pytest.mark.asyncio
    async def test_at_ceiling(self, db, config):
        user = await create_user(db, "cost2@test.com")
        article_id = await _create_article(db, user["id"])
        # Record costs up to ceiling
        await record_llm_cost(db, article_id, user["id"], "llm_call", cost_cents=250)
        result = await check_cost_ceiling(db, article_id, config.PER_ARTICLE_COST_CEILING_CENTS)
        assert result["under_ceiling"] is False
        assert result["total_cost_cents"] == 250

    @pytest.mark.asyncio
    async def test_over_ceiling(self, db, config):
        user = await create_user(db, "cost3@test.com")
        article_id = await _create_article(db, user["id"])
        await record_llm_cost(db, article_id, user["id"], "llm_call", cost_cents=300)
        result = await check_cost_ceiling(db, article_id, config.PER_ARTICLE_COST_CEILING_CENTS)
        assert result["under_ceiling"] is False
        assert result["total_cost_cents"] == 300


class TestRecordLlmCost:
    @pytest.mark.asyncio
    async def test_record_and_accumulate(self, db):
        user = await create_user(db, "cost4@test.com")
        article_id = await _create_article(db, user["id"])
        await record_llm_cost(db, article_id, user["id"], "llm_call", cost_cents=50)
        await record_llm_cost(db, article_id, user["id"], "llm_call", cost_cents=75)
        result = await check_cost_ceiling(db, article_id, 250)
        assert result["total_cost_cents"] == 125

    @pytest.mark.asyncio
    async def test_record_image_cost(self, db):
        user = await create_user(db, "cost5@test.com")
        article_id = await _create_article(db, user["id"])
        await record_llm_cost(db, article_id, user["id"], "image_generation", cost_cents=10)
        result = await check_cost_ceiling(db, article_id, 250)
        assert result["total_cost_cents"] == 10
