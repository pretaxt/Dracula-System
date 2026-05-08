"""仪表盘聚合服务。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.position import PositionRecord
from app.services.balance_service import get_per_exchange_equity, get_total_equity_usd


def _initial_capital() -> Decimal:
    """从 settings 读初始资本（env: INITIAL_CAPITAL_USD，默认 $300）。

    实盘下应反映真实账户 USDT 余额；P1+ 计划接 Binance fetch_balance 自动取值。
    """
    raw = get_settings().initial_capital_usd or "300"
    try:
        return Decimal(str(raw))
    except (ValueError, ArithmeticError):
        return Decimal("300")


# 兼容老 import 路径（account.py 直接拿这个常量）
# 注意：模块级求值——env 变更需重启容器生效
INITIAL_CAPITAL_USD = _initial_capital()


async def get_summary(session: AsyncSession, adapters: dict | None = None) -> dict:
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
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_funding = (
        await session.execute(
            select(func.coalesce(func.sum(PositionRecord.funding_received), 0)).where(
                PositionRecord.opened_at >= today_start
            )
        )
    ).scalar_one()

    # 当月净 PnL (month-to-date)
    month_start = today_start.replace(day=1)
    monthly_pnl_raw = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(PositionRecord.realized_pnl + PositionRecord.unrealized_pnl), 0
                )
            ).where(PositionRecord.opened_at >= month_start)
        )
    ).scalar_one()

    # 今日净 PnL (用于 daily_drawdown)
    today_pnl_raw = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(PositionRecord.realized_pnl + PositionRecord.unrealized_pnl), 0
                )
            ).where(PositionRecord.opened_at >= today_start)
        )
    ).scalar_one()

    # 本周净 PnL (从周一 00:00 起)
    week_start = today_start - timedelta(days=today_start.weekday())
    weekly_pnl_raw = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(PositionRecord.realized_pnl + PositionRecord.unrealized_pnl), 0
                )
            ).where(PositionRecord.opened_at >= week_start)
        )
    ).scalar_one()

    # 当前占用保证金 (open positions)
    margin_used_total = (
        await session.execute(
            select(func.coalesce(func.sum(PositionRecord.margin_used), 0)).where(
                PositionRecord.status == "open"
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

    # 30 天 PnL 序列
    series = await _pnl_series_30d(session)

    # ---- 估算字段 ----
    net_pnl = Decimal(str(r_pnl)) + Decimal(str(u_pnl))
    # 优先取真实账户聚合（spot+USDM），拉不到 fallback 到 env
    real_balance: Decimal | None = None
    per_exchange_equity: dict[str, str] = {}
    if adapters:
        real_balance = await get_total_equity_usd(adapters)
        per_ex = await get_per_exchange_equity(adapters)
        per_exchange_equity = {
            ex: str(round(v, 2)) for ex, v in per_ex.items()
        }
    total_equity = real_balance if real_balance is not None else _initial_capital() + net_pnl

    today_pnl = Decimal(str(today_pnl_raw))
    daily_drawdown_pct = (
        (today_pnl / total_equity * Decimal("100"))
        if total_equity > 0
        else Decimal("0")
    )

    weekly_pnl = Decimal(str(weekly_pnl_raw))
    weekly_dd_pct = (
        (weekly_pnl / total_equity * Decimal("100"))
        if total_equity > 0
        else Decimal("0")
    )

    margin_used = Decimal(str(margin_used_total))
    margin_usage_pct = (
        (margin_used / total_equity * Decimal("100"))
        if total_equity > 0
        else Decimal("0")
    )

    # P2 待接 — 真实 5min API 错误率 / WS 稳定性需要监控埋点。
    # 当前没有错误就用 100% / 0%(诚实占位,等接入 metrics 后改真值)。
    api_error_rate_5m_pct = Decimal("0")
    ws_stability_pct = Decimal("100")

    strategy_perf = await _get_strategy_performance(session)

    return {
        "net_pnl_usd": str(round(net_pnl, 8)),
        "realized_pnl_usd": str(round(Decimal(str(r_pnl)), 8)),
        "unrealized_pnl_usd": str(round(Decimal(str(u_pnl)), 8)),
        "today_funding_usd": str(round(Decimal(str(today_funding)), 8)),
        "monthly_pnl_usd": str(round(Decimal(str(monthly_pnl_raw)), 8)),
        "daily_drawdown_pct": str(round(daily_drawdown_pct, 4)),
        "weekly_dd_pct": str(round(weekly_dd_pct, 4)),
        "margin_usage_pct": str(round(margin_usage_pct, 4)),
        "api_error_rate_5m_pct": str(round(api_error_rate_5m_pct, 4)),
        "ws_stability_pct": str(round(ws_stability_pct, 4)),
        "total_equity_usd": str(round(total_equity, 2)),
        "open_positions": open_count,
        "avg_apr_pct": str(round(avg_apr, 4)),
        "pnl_series_30d": series,
        "strategy_performance": strategy_perf,
        "equity_by_exchange": per_exchange_equity,
    }


# ---------------------------------------------------------------------------
# 按策略实例聚合 PnL (B.3)
# ---------------------------------------------------------------------------

_STRATEGY_LABEL_MAP: dict[str, str] = {
    "funding_rate_main": "资金费率套利",
    "spot_perp_main": "期现套利",
}


async def _get_strategy_performance(session: AsyncSession) -> list[dict]:
    """按 strategy_instance 聚合 realized + unrealized PnL + 持仓数。"""
    stmt = text(
        """
        SELECT strategy_instance,
               COALESCE(SUM(realized_pnl), 0)   AS realized,
               COALESCE(SUM(unrealized_pnl), 0) AS unrealized,
               COUNT(CASE WHEN status = 'open'   THEN 1 END) AS open_cnt,
               COUNT(CASE WHEN status = 'closed' THEN 1 END) AS closed_cnt
        FROM positions
        WHERE strategy_instance IS NOT NULL
        GROUP BY strategy_instance
        ORDER BY (COALESCE(SUM(realized_pnl),0) + COALESCE(SUM(unrealized_pnl),0)) DESC
        """
    )
    rows = (await session.execute(stmt)).fetchall()
    out: list[dict] = []
    for r in rows:
        realized = Decimal(str(r.realized))
        unrealized = Decimal(str(r.unrealized))
        out.append({
            "instance": r.strategy_instance,
            "label": _STRATEGY_LABEL_MAP.get(r.strategy_instance, r.strategy_instance),
            "realized_pnl": str(round(realized, 8)),
            "unrealized_pnl": str(round(unrealized, 8)),
            "total_pnl": str(round(realized + unrealized, 8)),
            "open_positions": int(r.open_cnt),
            "closed_positions": int(r.closed_cnt),
        })
    return out


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
    return [
        {"date": str(r.day), "net_pnl_usd": str(round(Decimal(str(r.net_pnl)), 8))}
        for r in rows
    ]
