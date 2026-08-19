"""Tests for rate limiter (Task 3.1)."""

import asyncio
import time

import pytest

from app.llm.rate_limiter import TokenBucketRateLimiter, RateLimiterRegistry


class TestTokenBucketRateLimiter:
    def test_initial_tokens(self):
        limiter = TokenBucketRateLimiter(tokens_per_second=10, max_tokens=100)
        assert limiter.available_tokens() == 100

    @pytest.mark.asyncio
    async def test_acquire_within_budget(self):
        limiter = TokenBucketRateLimiter(tokens_per_second=10, max_tokens=100)
        acquired = await limiter.acquire(50)
        assert acquired is True
        assert abs(limiter.available_tokens() - 50) < 1  # allow tiny refill drift

    @pytest.mark.asyncio
    async def test_acquire_all_tokens(self):
        limiter = TokenBucketRateLimiter(tokens_per_second=10, max_tokens=100)
        acquired = await limiter.acquire(100)
        assert acquired is True
        assert limiter.available_tokens() < 1  # effectively zero, tiny refill drift ok

    @pytest.mark.asyncio
    async def test_acquire_exceeds_budget_waits(self):
        """If tokens are depleted, acquire should wait for refill."""
        limiter = TokenBucketRateLimiter(tokens_per_second=100, max_tokens=100)
        await limiter.acquire(100)  # drain
        start = time.monotonic()
        acquired = await limiter.acquire(10, timeout=2.0)
        elapsed = time.monotonic() - start
        assert acquired is True
        assert elapsed < 1.0  # refill is fast at 100/s

    @pytest.mark.asyncio
    async def test_acquire_timeout(self):
        """If refill is too slow, acquire times out."""
        limiter = TokenBucketRateLimiter(tokens_per_second=1, max_tokens=10)
        await limiter.acquire(10)  # drain
        acquired = await limiter.acquire(10, timeout=0.1)
        assert acquired is False

    def test_refill(self):
        limiter = TokenBucketRateLimiter(tokens_per_second=1000, max_tokens=100)
        limiter._tokens = 0
        limiter._last_refill = time.monotonic() - 0.1  # 100ms ago
        limiter._refill()
        assert limiter.available_tokens() >= 90  # ~100 tokens refilled

    def test_max_tokens_cap(self):
        limiter = TokenBucketRateLimiter(tokens_per_second=1000, max_tokens=50)
        limiter._tokens = 0
        limiter._last_refill = time.monotonic() - 10  # 10 seconds ago
        limiter._refill()
        assert limiter.available_tokens() == 50  # capped at max


class TestRateLimiterRegistry:
    def test_get_or_create(self):
        registry = RateLimiterRegistry()
        limiter1 = registry.get("openai")
        limiter2 = registry.get("openai")
        assert limiter1 is limiter2

    def test_different_providers(self):
        registry = RateLimiterRegistry()
        openai = registry.get("openai")
        anthropic = registry.get("anthropic")
        assert openai is not anthropic

    def test_custom_config(self):
        registry = RateLimiterRegistry(configs={
            "openai": {"tokens_per_second": 50, "max_tokens": 500},
        })
        limiter = registry.get("openai")
        assert limiter.available_tokens() == 500
