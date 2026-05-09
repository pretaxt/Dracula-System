"""应用层限频 — Token Bucket 实现

在 CCXT 自带的 enableRateLimit 之上再加一层应用级保护:
  - CCXT 的限频是被动的(超了等待),这里是主动的(提前限速)
  - 比 CCXT 更保守,留有安全余量

设计来源: docs/07_exchange_adapters.md §3.5

每个交易所真实参考限速 (CCXT 默认 / 文档):
  Binance Spot : 1200 req/min (weight=1 的基础接口)
  Binance USDM : 2400 req/min
  Bybit        : 600  req/min
  OKX          : 600  req/min
  HTX          : 800  req/min
  Bitget       : 600  req/min

v0.4.3 加入跨策略全局共享池:
  ``get_global_limiter(exchange_id)`` 返回单例 limiter，所有策略 / adapter 实例
  共享同一个桶 → 12 策略全开也不会撞 exchange limit。
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


# ---------------------------------------------------------------------------
# 全局共享池（跨策略 / 跨 adapter 实例）
# ---------------------------------------------------------------------------


# 每个 exchange 全局预算（留 30%+ 余量，低于真实 limit 防 429）
# key 必须与 ccxt_base 的 _exchange_id 一致（小写）
_GLOBAL_RATE_LIMITS: dict[str, int] = {
    "binance": 1000,        # spot 1200, 留 17% 余量
    "binanceusdm": 2000,    # usdm 2400, 留 17% 余量
    "okx": 400,             # 真实 600, 留 33% 余量（实测最敏感）
    "bybit": 500,           # 真实 600
    "htx": 700,             # 真实 800
    "bitget": 500,          # 真实 600
}

_GLOBAL_LIMITERS: dict[str, TokenBucketRateLimiter] = {}
_GLOBAL_LOCK = asyncio.Lock() if False else None  # 占位；实际首次创建时按需取


def get_global_limiter(
    exchange_id: str, default_rpm: int = 600,
) -> TokenBucketRateLimiter:
    """跨策略 / 跨 adapter 共享的 limiter（单例 per exchange）。

    ``exchange_id`` 大小写不敏感；未在表中的交易所用 ``default_rpm``。

    线程安全说明: 首次创建走 dict.setdefault 原子性，多协程并发首次访问最多
    临时创建一两个多余 limiter（瞬态浪费），不会破坏后续单例行为。
    """
    key = (exchange_id or "").lower()
    existing = _GLOBAL_LIMITERS.get(key)
    if existing is not None:
        return existing
    rpm = _GLOBAL_RATE_LIMITS.get(key, default_rpm)
    limiter = TokenBucketRateLimiter(max_rpm=rpm)
    return _GLOBAL_LIMITERS.setdefault(key, limiter)


def reset_global_limiters() -> None:
    """重置全局池（仅测试 / 重启钩子用）。"""
    _GLOBAL_LIMITERS.clear()
