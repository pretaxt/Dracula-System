"""异步数据库会话工厂

用法::

    async with get_session() as session:
        session.add(obj)
        # commit 由 context-manager 自动完成

FastAPI 依赖注入::

    async def endpoint(db: AsyncSession = Depends(get_db)):
        ...
"""
from __future__ import annotations

import functools
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """所有 ORM 模型的公共基类。"""


# ---------------------------------------------------------------------------
# Engine & session factory (cached singletons)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _build_engine():
    settings = get_settings()
    url = settings.database_url
    engine = create_async_engine(
        url,
        echo=settings.environment == "development",
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    logger.info("database_engine_created", url=url.split("@")[-1])  # hide credentials
    return engine


@functools.lru_cache(maxsize=1)
def _build_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        _build_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """异步上下文管理器，自动 commit / rollback。"""
    async with _build_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI `Depends` 注入用的生成器。"""
    async with get_session() as session:
        yield session
