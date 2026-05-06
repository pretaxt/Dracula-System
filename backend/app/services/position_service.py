"""仓位服务 — 查询、分页、手动平仓。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.position import PositionRecord


async def list_positions(
    session: AsyncSession,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PositionRecord], int]:
    stmt = select(PositionRecord)
    if status:
        stmt = stmt.where(PositionRecord.status == status)
    stmt = stmt.order_by(PositionRecord.opened_at.desc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows), total


async def get_position(session: AsyncSession, uuid: str) -> PositionRecord | None:
    stmt = select(PositionRecord).where(PositionRecord.uuid == uuid)
    return (await session.execute(stmt)).scalar_one_or_none()


def compute_days_held(rec: PositionRecord) -> Decimal:
    if rec.opened_at is None:
        return Decimal("0")
    end = rec.closed_at or datetime.now(timezone.utc)
    delta = end - rec.opened_at
    return Decimal(str(round(delta.total_seconds() / 86400, 4)))
