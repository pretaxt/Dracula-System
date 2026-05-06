"""资金费率服务 — 读 Redis 快照 + 查询历史。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import get_redis


async def get_opportunities() -> tuple[list[dict], datetime | None]:
    """从 Redis 读取最新机会快照。"""
    client = get_redis()
    raw = await client.get("dracula:funding_rate:opportunities")
    if not raw:
        return [], None
    data = json.loads(raw)
    snapshot_at = datetime.now(timezone.utc)
    return data if isinstance(data, list) else [], snapshot_at


async def get_funding_history(
    session: AsyncSession,
    *,
    exchange: str | None = None,
    symbol: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    page: int = 1,
    page_size: int = 100,
) -> tuple[list, int]:
    """查询历史资金费率记录（时间窗口最大 90 天）。"""
    from app.models.funding_rate import FundingRateRecord  # local import avoids circular

    stmt = select(FundingRateRecord)
    if exchange:
        stmt = stmt.where(FundingRateRecord.exchange == exchange)
    if symbol:
        stmt = stmt.where(FundingRateRecord.symbol == symbol)
    if from_dt:
        stmt = stmt.where(FundingRateRecord.time >= from_dt)
    if to_dt:
        stmt = stmt.where(FundingRateRecord.time <= to_dt)

    stmt = stmt.order_by(FundingRateRecord.time.desc())
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar_one()
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows), total
