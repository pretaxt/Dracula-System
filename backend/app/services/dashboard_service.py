"""仪表盘聚合服务。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.position import PositionRecord


async def get_summary(session: AsyncSession) -> dict:
    # 总 realized PnL
    r_pnl = (
        await session.execute(
            select(func.coalesce(func.sum(PositionRecord.realized_pnl), 0))
        )
    ).scalar_one()

    # 总 unrealized PnL（仅 open）
    u_pnl = (
        await session.execute(
            select(func.coalesce(func.sum(PositionRecord.unrealized_pnl), 0)).where(
                PositionRecord.status == "open"
            )
        )
    ).scalar_one()

    # 今日资金费率收入
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_funding = (
        await session.execute(
            select(func.coalesce(func.sum(PositionRecord.funding_received), 0)).where(
                PositionRecord.opened_at >= today_start
            )
        )
    ).scalar_one()

    # 开仓数 & 平均 APR
    open_rows = (
        await session.execute(
            select(PositionRecord.target_apr_pct).where(PositionRecord.status == "open")
        )
    ).scalars().all()

    open_count = len(open_rows)
    avg_apr = (
        sum(float(a) for a in open_rows if a) / open_count if open_count else 0.0
    )

    # 30 天 PnL 序列（每日净 PnL 累计）
    series = await _pnl_series_30d(session)

    return {
        "net_pnl_usd": str(round(Decimal(str(r_pnl)) + Decimal(str(u_pnl)), 8)),
        "realized_pnl_usd": str(round(Decimal(str(r_pnl)), 8)),
        "unrealized_pnl_usd": str(round(Decimal(str(u_pnl)), 8)),
        "today_funding_usd": str(round(Decimal(str(today_funding)), 8)),
        "open_positions": open_count,
        "avg_apr_pct": str(round(avg_apr, 4)),
        "pnl_series_30d": series,
    }


async def _pnl_series_30d(session: AsyncSession) -> list[dict]:
    since = date.today() - timedelta(days=29)
    stmt = text(
        """
        SELECT DATE(opened_at AT TIME ZONE 'UTC') AS day,
               SUM(realized_pnl + unrealized_pnl) AS net_pnl
        FROM positions
        WHERE opened_at >= :since
        GROUP BY day
        ORDER BY day
        """
    )
    rows = (await session.execute(stmt, {"since": since})).fetchall()
    return [{"date": str(r.day), "net_pnl_usd": str(round(Decimal(str(r.net_pnl)), 8))} for r in rows]
