"""Redis 异步客户端单例

用法::

    client = get_redis()
    await client.publish("channel", payload)

    async with get_redis_context() as client:
        await client.set("key", "value", ex=60)
"""
from __future__ import annotations

import functools
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@functools.lru_cache(maxsize=1)
def get_redis() -> aioredis.Redis:
    """返回全局 Redis 客户端（惰性初始化）。"""
    settings = get_settings()
    client: aioredis.Redis = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )
    logger.info("redis_client_created", url=settings.redis_url)
    return client


@asynccontextmanager
async def get_redis_context() -> AsyncGenerator[aioredis.Redis, None]:
    """在需要显式关闭时使用（测试、脚本）。"""
    client = get_redis()
    try:
        yield client
    finally:
        await client.aclose()


async def publish(channel: str, message: str) -> int:
    """发布消息到 Redis 频道，返回接收者数量。"""
    client = get_redis()
    receivers: int = await client.publish(channel, message)
    return receivers
