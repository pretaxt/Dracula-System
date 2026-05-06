"""应用层限频 — Token Bucket 实现

在 CCXT 自带的 enableRateLimit 之上再加一层应用级保护:
  - CCXT 的限频是被动的(超了等待),这里是主动的(提前限速)
  - 比 CCXT 更保守,留有安全余量

设计来源: docs/07_exchange_adapters.md §3.5

每个交易所参考限速:
  Binance : 1200 req/min (weight=1 的基础接口)
  Bybit   : 600  req/min
  OKX     : 600  req/min
  HTX     : 800  req/min
  Bitget  : 600  req/min
"""
from __future__ import annotations

import asyncio
import time


class TokenBucketRateLimiter:
    """令牌桶限流器 (线程安全异步版本)

    按 max_rpm 设置每分钟最大请求数。
    burst_factor 控制短时突发倍数 (默认 1.0 = 不允许突发)。

    用法:
        limiter = TokenBucketRateLimiter(max_rpm=1200)
        await limiter.acquire()   # 调用前等待令牌
        result = await exchange.fetch_ticker(...)
    """

    def __init__(
        self,
        max_rpm: int,
        burst_factor: float = 1.0,
    ) -> None:
        if max_rpm <= 0:
            raise ValueError(f"max_rpm must be positive, got {max_rpm}")
        self._rate: float = max_rpm / 60.0          # tokens per second
        self._capacity: float = max_rpm * burst_factor / 60.0
        self._tokens: float = self._capacity        # 初始满桶
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        """等待直到有足够令牌,然后消耗 `tokens` 个令牌"""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self._capacity,
                    self._tokens + elapsed * self._rate,
                )
                self._last_refill = now

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return

                # 计算需要等待多久能凑够令牌
                wait_time = (tokens - self._tokens) / self._rate
                # 释放锁再 sleep,避免阻塞其他协程
                self._lock.release()
                try:
                    await asyncio.sleep(wait_time)
                finally:
                    await self._lock.acquire()

    @property
    def available_tokens(self) -> float:
        """当前可用令牌数(仅供监控,不保证原子性)"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        return min(self._capacity, self._tokens + elapsed * self._rate)
