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

    # 真正的今日 PnL (含跨日仓位)：
    #   = 今日 closed 的 realized + 当前 open 的 unrealized
    # 不再用 opened_at >= today_start 过滤，让跨日仓位也参与
    today_realized_raw = (
        await session.execute(
            select(func.coalesce(func.sum(PositionRecord.realized_pnl), 0))
            .where(PositionRecord.status == "closed")
            .where(PositionRecord.closed_at >= today_start)
        )
    ).scalar_one()
    today_unrealized_raw = (
        await session.execute(
            select(func.coalesce(func.sum(PositionRecord.unrealized_pnl), 0))
            .where(PositionRecord.status == "open")
        )
    ).scalar_one()
    # DB unrealized_pnl 字段未实时 mark-to-market（工程债务），改用 reconciler 真实持仓数据
    if reconciler is not None and getattr(reconciler, "position_cache", None):
        try:
            real_upnl = Decimal("0")
            for ex_positions in reconciler.position_cache.values():
                for pos_dict in ex_positions or []:
                    val = pos_dict.get("unrealized_pnl") or 0
                    real_upnl += Decimal(str(val))
            today_unrealized_raw = real_upnl
        except Exception:
            pass

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
            # binance 全 4 类账户:
            #   现货: USDT (spot) + USDT_SPOT_OTHERS (BNB/BTC/ETH 等折算)
            #   杠杆: USDT_MARGIN (全仓 USDT) + USDT_MARGIN_OTHERS (全仓非 USDT 抵押)
            #         + USDT_MARGIN_ISOLATED (逐仓 BTC-equiv → USDT)
            #   合约: USDT_PERP (U 本位) + USDT_PERP_COIN (币本位折算)
            #   资金: USDT_FUNDING (USDT) + USDT_FUNDING_OTHERS (非稳定币折算)
            # htx: USDT (spot) + USDT_HTX_SWAP（UTA swap 钱包，CCXT 默认拿不到走专用 API）
            # OKX UTA / bybit / bitget UTA 共享 trading account，仅 USDT
            usdt_keys = (
                "USDT",
                "USDT_SPOT_OTHERS",
                "USDT_MARGIN", "USDT_MARGIN_OTHERS", "USDT_MARGIN_ISOLATED",
                "USDT_PERP", "USDT_PERP_COIN",
                "USDT_FUNDING", "USDT_FUNDING_OTHERS",
                "USDT_HTX_SWAP",
            )
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
    # ⚠ 不再 fallback 到 balance_service.get_per_exchange_equity ─
    # 该旧路径 OKX 双倍 + HTX 漏 swap，会让 reconciler 启动 30s 内的 cold-start
    # 窗口产生异常 concentration (sym_conc 68%、ex_conc 269% 等)。
    # 现在 reconciler 未就绪 → real_balance 保持 None → 下游用 net_pnl 兜底，
    # concentration 指标分母用 sum(open notional)，circuit_breaker 不会误熔断。
    total_equity = real_balance if real_balance is not None else _initial_capital() + net_pnl
    # reconciler 未就绪标记 — 让 concentration 计算放弃（避免 _initial_capital 兜底
    # 太小导致 sym_conc/ex_conc 假阳性触发 circuit_breaker）。
    balance_data_ready = real_balance is not None

    # 余额同步诊断字段：列出"无 trading 凭据"的 CEX，UI 提示用户去 设置 → 交易所凭据
    missing_credentials_exchanges: list[str] = []
    if adapters:
        for ex_name, ad in adapters.items():
            if not getattr(ad, "_api_key", "") or "":
                missing_credentials_exchanges.append(ex_name)

    today_pnl = Decimal(str(today_pnl_raw))
    today_realized = Decimal(str(today_realized_raw))
    today_unrealized = Decimal(str(today_unrealized_raw))
    today_pnl_real = today_realized + today_unrealized
    # 2026-05-14: reconciler 未就绪时跳过 dd 计算，避免 _initial_capital($300) 兜底
    # 分母太小导致 weekly_dd 假阳性 -8.9% / -9.3% 误触发 circuit_breaker。
    # 跟下面 concentration 计算用同样的 balance_data_ready 守护。
    if balance_data_ready and total_equity > 0:
        daily_drawdown_pct = today_pnl / total_equity * Decimal("100")
    else:
        daily_drawdown_pct = Decimal("0")

    weekly_pnl = Decimal(str(weekly_pnl_raw))
    if balance_data_ready and total_equity > 0:
        weekly_dd_pct = weekly_pnl / total_equity * Decimal("100")
    else:
        weekly_dd_pct = Decimal("0")

    margin_used = Decimal(str(margin_used_total))
    # 同 weekly_dd / daily_dd：reconciler 未就绪时跳过，避免 $300 兜底分母假阳性
    if balance_data_ready and total_equity > 0:
        margin_usage_pct = margin_used / total_equity * Decimal("100")
    else:
        margin_usage_pct = Decimal("0")

    # 真实 5min HTTP 错误率（in-memory metrics 滑窗）
    from app.core.metrics import get_metrics  # noqa: PLC0415
    metrics = get_metrics()
    api_error_rate_5m_pct = Decimal(str(round(metrics.http_error_rate_5m_pct(), 4)))
    # WS 稳定性：暂用 100 - HTTP 错误率作为粗略 health 指标，后续接 watcher 心跳
    ws_stability_pct = max(Decimal("0"), Decimal("100") - api_error_rate_5m_pct)

    strategy_perf = await _get_strategy_performance(session)

    sharpe_30d = _annualized_sharpe(series, total_equity)
    # reconciler 未就绪 → 不计算 concentration（避免误熔断），等下个 30s 周期
    if balance_data_ready:
        max_ex_conc = _max_exchange_concentration(per_exchange_equity, total_equity)
        max_sym_conc = await _max_symbol_concentration(session, total_equity)
    else:
        max_ex_conc = Decimal("0")
        max_sym_conc = Decimal("0")

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
        "today_pnl_usd": str(round(today_pnl_real, 8)),
        "today_realized_usd": str(round(today_realized, 8)),
        "today_unrealized_usd": str(round(today_unrealized, 8)),
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
        "missing_credentials_exchanges": missing_credentials_exchanges,
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
    """单交易所最大占比 %，total_equity 缺失或 0 → 0。

    分母用 max(total_equity, sum(per_exchange)) 防止两条数据源不一致
    （reconciler 启动瞬间 fallback 到旧 path，OKX 双倍 / HTX 漏 swap → 比值 > 100%）。
    """
    if not per_exchange or total_equity <= 0:
        return Decimal("0")
    try:
        values = [Decimal(str(v)) for v in per_exchange.values()]
        max_eq = max(values)
        sum_eq = sum(values, Decimal("0"))
    except (ValueError, ArithmeticError):
        return Decimal("0")
    # 用两者较大值作分母 — 自我校正：若 per_exchange 总和 > total_equity（双倍计算
    # 等异常），用 sum_eq 让比值始终 ≤ 100%。
    denom = max(total_equity, sum_eq)
    pct = max_eq / denom * Decimal("100")
    # 终极兜底：超 100% 强制截到 100%（不可能的物理状态）
    return min(pct, Decimal("100"))


async def _max_symbol_concentration(
    session: AsyncSession, total_equity: Decimal | None = None,
) -> Decimal:
    """单币种最大占比 % = max(per-symbol notional) / 账户总权益 × 100。

    分母用 **账户总权益**（不是 sum of open notionals），否则单笔持仓永远 100%。
    20% 阈值 = 单币种最大风险敞口 ≤ 总权益的 20%（如 $500 账户单 TIA ≤ $100）。
    """
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
    for notes, notional in rows:
        if notional is None:
            continue
        sym = (notes or "").split("\n", 1)[0].split("@", 1)[0].strip() or "?"
        n = Decimal(str(notional))
        by_sym[sym] = by_sym.get(sym, Decimal("0")) + n
    if not by_sym:
        return Decimal("0")
    # 异常防护：若 total_equity 偏小（reconciler 启动瞬间 fallback 只读到部分
    # 交易所余额），分母至少取 max(total_equity, sum of all positions notional)。
    # 同时 100% 是物理上限，保证不出现 >100% 假报警。
    max_sym = max(by_sym.values())
    total_open = sum(by_sym.values())
    denom_candidates = [
        d for d in (total_equity, total_open) if d and d > 0
    ]
    denom = max(denom_candidates) if denom_candidates else Decimal("0")
    if denom <= 0:
        return Decimal("0")
    pct = max_sym / denom * Decimal("100")
    return min(pct, Decimal("100"))


# ---------------------------------------------------------------------------
# 按策略实例聚合 PnL (B.3)
# ---------------------------------------------------------------------------

_STRATEGY_LABEL_MAP: dict[str, str] = {
    "funding_rate_main": "资金费率套利",       # #01
    "perp_basis_main": "跨所基差套利",         # #02
    "price_spread_main": "价差套利",           # #03
    "spot_perp_main": "期现套利",              # #04
    "cex_dex_main": "CEX-DEX 套利",            # #05
    "triangular_main": "三角套利",             # #06
    "stablecoin_main": "稳定币套利",
    "options_vol_main": "期权波动率",
    "pairs_main": "配对套利",
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
