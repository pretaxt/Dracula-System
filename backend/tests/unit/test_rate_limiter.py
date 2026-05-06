"""单元测试 — exchanges/rate_limiter.py"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.exchanges.rate_limiter import TokenBucketRateLimiter


class TestTokenBucketRateLimiter:
    def test_init_invalid_rpm_raises(self):
        with pytest.raises(ValueError, match="max_rpm must be positive"):
            TokenBucketRateLimiter(max_rpm=0)

    def test_available_tokens_full_on_init(self):
        limiter = TokenBucketRateLimiter(max_rpm=60)
        # capacity = 60/60 = 1.0 token; bucket starts full
        assert limiter.available_tokens >= 1.0

    @pytest.mark.asyncio
    async def test_acquire_single_token_immediate(self):
        """First acquire should return immediately when bucket is full"""
        limiter = TokenBucketRateLimiter(max_rpm=600)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_acquire_reduces_available_tokens(self):
        limiter = TokenBucketRateLimiter(max_rpm=600)
        tokens_before = limiter.available_tokens
        await limiter.acquire()
        tokens_after = limiter.available_tokens
        assert tokens_after < tokens_before

    @pytest.mark.asyncio
    async def test_multiple_concurrent_acquires_all_complete(self):
        """All concurrent acquires eventually complete"""
        limiter = TokenBucketRateLimiter(max_rpm=6000)  # 100 req/s
        results = []

        async def worker(i: int) -> None:
            await limiter.acquire()
            results.append(i)

        await asyncio.gather(*[worker(i) for i in range(5)])
        assert sorted(results) == [0, 1, 2, 3, 4]

    @pytest.mark.asyncio
    async def test_high_rpm_does_not_throttle_small_burst(self):
        """High-RPM limiter should not meaningfully delay a small burst"""
        limiter = TokenBucketRateLimiter(max_rpm=12000)  # 200 req/s
        start = time.monotonic()
        for _ in range(10):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        # 10 acquires at 200 req/s = 0.05 s ideal; allow 1 s margin
        assert elapsed < 1.0

    def test_burst_factor_increases_capacity(self):
        limiter = TokenBucketRateLimiter(max_rpm=60, burst_factor=2.0)
        # capacity = 60 * 2.0 / 60 = 2.0 tokens
        assert limiter.available_tokens >= 2.0
