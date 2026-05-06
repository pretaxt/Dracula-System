"""日志系统 — structlog

输出格式:
  development: 彩色可读文本
  production:  JSON（每行一条，便于日志收集）

关键字段 (来自 docs/11_deployment_and_ops.md):
  strategy / exchange / symbol / event / level / timestamp
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import get_settings


def setup_logging() -> None:
    """初始化 structlog，应在进程启动时调用一次"""
    settings = get_settings()
    is_dev = settings.is_development
    log_level = getattr(logging, settings.log_level, logging.INFO)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if is_dev:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # 降低第三方库噪音
    for noisy in ("sqlalchemy.engine", "alembic", "ccxt", "asyncio", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """
    获取 logger。推荐用法:

        logger = get_logger(__name__)
        logger.info("order_placed",
                    strategy="funding_rate_main",
                    exchange="binance",
                    symbol="BTC/USDT",
                    event="order_placed",
                    size="0.01")
    """
    return structlog.get_logger(name)
