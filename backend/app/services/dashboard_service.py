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


async def get_summary(
    session: AsyncSession,
    adapters: dict | None = None,
    reconciler: object | None = None,
) -> dict:
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
    # R6: 优先 reconciler cache（30s 周期更新，已经聚合 spot+margin+perp）
    if reconciler is not None and getattr(reconciler, "balance_cache", None):
        try:
            cache = reconciler.balance_cache
            total = Decimal("0")
            per_ex_local: dict[str, Decimal] = {}
            # binance 含 USDT (spot) + USDT_MARGIN (cross-margin) + USDT_PERP (USDM) + USDT_FUNDING (Pay)
            # OKX UTA 共享 trading account，仅 USDT
            usdt_keys = ("USDT", "USDT_MARGIN", "USDT_PERP", "USDT_FUNDING")
            for ex_name, assets in cache.items():
                ex_total = Decimal("0")
                for key in usdt_keys:
                    info = (assets or {}).get(key)
                    if info:
                        ex_total += Decimal(str(info.get("total") or 0))
                if ex_total > 0:
                    per_ex_local[ex_name] = ex_total
                    total += ex_total
            if total > 0:
                real_balance = total
                per_exchange_equity = {
                    ex: str(round(v, 2)) for ex, v in per_ex_local.items()
                }
        except Exception:
            pass
    # 降级：reconciler 不可用 → lazy fetch
    if real_balance is None and adapters:
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

    # 真实 5min HTTP 错误率（in-memory metrics 滑窗）
    from app.core.metrics import get_metrics  # noqa: PLC0415
    metrics = get_metrics()
    api_error_rate_5m_pct = Decimal(str(round(metrics.http_error_rate_5m_pct(), 4)))
    # WS 稳定性：暂用 100 - HTTP 错误率作为粗略 health 指标，后续接 watcher 心跳
    ws_stability_pct = max(Decimal("0"), Decimal("100") - api_error_rate_5m_pct)

    strategy_perf = await _get_strategy_performance(session)

    sharpe_30d = _annualized_sharpe(series, total_equity)
    max_ex_conc = _max_exchange_concentration(per_exchange_equity, total_equity)
    max_sym_conc = await _max_symbol_concentration(session)

    # 运维指标（5min 滑窗 in-memory）
    api_p95_ms = round(metrics.http_latency_p95_ms(), 1)
    scan_perf = {
        "funding_rate": {
            "p95_ms": round(metrics.scan_p95_ms("funding_rate"), 1),
            "count_5m": metrics.scan_count_5m("funding_rate"),
        },
        "spot_perp": {
            "p95_ms": round(metrics.scan_p95_ms("spot_perp"), 1),
            "count_5m": metrics.scan_count_5m("spot_perp"),
        },
        "perp_basis": {
            "p95_ms": round(metrics.scan_p95_ms("perp_basis"), 1),
            "count_5m": metrics.scan_count_5m("perp_basis"),
        },
    }
    ccxt_health = {}
    for ex in ("binance", "okx", "binanceusdm", "bitget", "bybit", "htx"):
        ccxt_health[ex] = {
            "calls_5m": metrics.ccxt_call_count_5m(ex),
            "error_rate_pct": round(metrics.ccxt_error_rate_5m_pct(ex), 2),
        }
    # Market Data Hub health（v0.4.5：跨策略共享行情缓存）
    from app.core.market_data_hub import get_market_data_hub  # noqa: PLC0415
    hub = get_market_data_hub()
    hub_per_ex = hub.health() if hub is not None else {}
    # 聚合 per-exchange → 顶层字段（schema 兼容）
    total_tickers = sum((v.get("ticker_count") or 0) for v in hub_per_ex.values())
    total_funding = sum((v.get("funding_count") or 0) for v in hub_per_ex.values())
    ticker_ages = [v.get("ticker_age_s") for v in hub_per_ex.values() if v.get("ticker_age_s") is not None]
    age_s = max(ticker_ages) if ticker_ages else None
    hub_health = {
        "ticker_count": total_tickers,
        "funding_count": total_funding,
        "age_s": age_s,
        "per_exchange": hub_per_ex,
    }

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
        "sharpe_30d": str(round(sharpe_30d, 3)),
        "max_exchange_concentration_pct": str(round(max_ex_conc, 2)),
        "max_symbol_concentration_pct": str(round(max_sym_conc, 2)),
        "api_latency_p95_ms": str(api_p95_ms),
        "scan_perf": scan_perf,
        "ccxt_health": ccxt_health,
        "market_data_hub": hub_health,
    }


# ---------------------------------------------------------------------------
# 风险 / 表现 衍生指标
# ---------------------------------------------------------------------------


def _annualized_sharpe(series: list[dict], total_equity: Decimal) -> Decimal:
    """30 天年化 Sharpe ratio（基于日 PnL/equity 算）。

    数据点 < 2 或 std=0 时返回 0。无风险收益假设为 0（加密无国债基准）。
    """
    import math  # noqa: PLC0415
    if len(series) < 2 or total_equity <= 0:
        return Decimal("0")
    eq = float(total_equity)
    rets: list[float] = []
    for p in series:
        try:
            pnl = float(p.get("net_pnl_usd", 0))
            rets.append(pnl / eq)
        except (TypeError, ValueError):
            continue
    if len(rets) < 2:
        return Decimal("0")
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    std = math.sqrt(var)
    if std == 0:
        return Decimal("0")
    sharpe = (mean / std) * math.sqrt(365)  # 年化
    return Decimal(str(sharpe))


def _max_exchange_concentration(
    per_exchange: dict[str, str], total_equity: Decimal
) -> Decimal:
    """单交易所最大占比 %，total_equity 缺失或 0 → 0。"""
    if not per_exchange or total_equity <= 0:
        return Decimal("0")
    try:
        max_eq = max(Decimal(str(v)) for v in per_exchange.values())
    except (ValueError, ArithmeticError):
        return Decimal("0")
    return max_eq / total_equity * Decimal("100")


async def _max_symbol_concentration(session: AsyncSession) -> Decimal:
    """单币种最大占比 % (max symbol open notional / total open notional)。"""
    rows = (
        await session.execute(
            select(PositionRecord.notes, PositionRecord.notional_usd).where(
                PositionRecord.status == "open"
            )
        )
    ).all()
    if not rows:
        return Decimal("0")
    by_sym: dict[str, Decimal] = {}
    total = Decimal("0")
    for notes, notional in rows:
        if notional is None:
            continue
        # notes 首行是 symbol（兼容 D.1+ 多行 + 旧记录）
        sym = (notes or "").split("\n", 1)[0].split("@", 1)[0].strip() or "?"
        n = Decimal(str(notional))
        by_sym[sym] = by_sym.get(sym, Decimal("0")) + n
        total += n
    if total <= 0 or not by_sym:
        return Decimal("0")
    return max(by_sym.values()) / total * Decimal("100")


# ---------------------------------------------------------------------------
# 按策略实例聚合 PnL (B.3)
# ---------------------------------------------------------------------------

_STRATEGY_LABEL_MAP: dict[str, str] = {
    "funding_rate_main": "资金费率套利",
    "spot_perp_main": "期现套利",
    "perp_basis_main": "跨所基差套利",
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
