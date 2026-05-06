"""Alembic 环境配置 — 异步版本 (asyncpg)"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Week 3+ 引入 ORM 模型后取消注释:
# from app.models.base import Base
# target_metadata = Base.metadata
target_metadata = None


def get_url() -> str:
    return (
        os.environ.get("DATABASE_URL")
        or "postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}".format(
            user=os.environ.get("POSTGRES_USER", "dracula"),
            password=os.environ.get("POSTGRES_PASSWORD", ""),
            host=os.environ.get("POSTGRES_HOST", "localhost"),
            port=os.environ.get("POSTGRES_PORT", "5432"),
            db=os.environ.get("POSTGRES_DB", "dracula"),
        )
    )


def run_migrations_offline() -> None:
    """离线模式：生成 SQL，不连接数据库"""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
