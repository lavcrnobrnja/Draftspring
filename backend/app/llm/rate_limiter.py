"""Token bucket rate limiter per LLM provider."""

import asyncio
import time


class TokenBucketRateLimiter:
    """Token bucket algorithm for rate limiting API calls."""

    def __init__(self, tokens_per_second: float = 10, max_tokens: int = 100):
        self._tokens_per_second = tokens_per_second
        self._max_tokens = max_tokens
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._max_tokens,
            self._tokens + elapsed * self._tokens_per_second,
        )
        self._last_refill = now

    def available_tokens(self) -> float:
        self._refill()
        return self._tokens

    async def acquire(self, tokens: int = 1, timeout: float = 30.0) -> bool:
        """Acquire tokens. Returns True if acquired, False if timed out."""
        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False

            # Wait for estimated refill time
            needed = tokens - self._tokens
            wait_time = min(needed / self._tokens_per_second, remaining)
            await asyncio.sleep(max(0.01, wait_time))


class RateLimiterRegistry:
    """Registry of per-provider rate limiters."""

    DEFAULT_CONFIGS = {
        "openai": {"tokens_per_second": 20, "max_tokens": 200},
        "anthropic": {"tokens_per_second": 10, "max_tokens": 100},
        "gemini": {"tokens_per_second": 5, "max_tokens": 50},
        "nano_banana": {"tokens_per_second": 5, "max_tokens": 30},
    }

    def __init__(self, configs: dict | None = None):
        self._configs = configs or self.DEFAULT_CONFIGS
        self._limiters: dict[str, TokenBucketRateLimiter] = {}

    def get(self, provider: str) -> TokenBucketRateLimiter:
        if provider not in self._limiters:
            cfg = self._configs.get(provider, {"tokens_per_second": 10, "max_tokens": 100})
            self._limiters[provider] = TokenBucketRateLimiter(**cfg)
        return self._limiters[provider]
