"""全局风控熔断器 — 在策略入场前 enforcement 账户级硬红线。

之前的设计漏洞：daily/weekly drawdown 红线只在 dashboard 显示，没有阻断 runner
开仓。账户已亏 -3%，runner 仍每 60s 开新仓 — 这是必须修复的 P0 风控漏洞。

本模块提供单一入口 `check_circuit_breakers()`，所有 paper_trading session 在
_open_position 前必须调用：触发任一硬红线时返回 `allow=False`，阻断新开仓。

B3 修复（依据 risk-manager + code-reviewer 双审）:
- 数据源 get_summary 必须传 reconciler/adapters，让 daily_dd 分母用真实账户余额
  （之前 fallback 到 _initial_capital + net_pnl 与 UI 不一致）
- DB 故障时改 fail-closed（之前 fail-open 让"DB 挂了反而开仓"成为反风控）
- 加 binance USDM 真实 maintenance_margin_ratio 监控（DB margin_used 与交易所
  内部 MMR 是两个概念，DB 看不到强平线临近）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 硬红线阈值（与「锁定红线·账户级」UI 显示对齐）
# ---------------------------------------------------------------------------

DAILY_DD_HALT_PCT = Decimal("-3.0")      # 单日 PnL ≤ -3% 即熔断
WEEKLY_DD_HALT_PCT = Decimal("-8.0")     # 周 PnL ≤ -8% 即熔断
MIN_MARGIN_USAGE_PCT = Decimal("50.0")   # DB 口径 margin_used / equity ≥ 50% 即熔断
MAX_EXCHANGE_CONCENTRATION_PCT = Decimal("50.0")
MAX_SYMBOL_CONCENTRATION_PCT = Decimal("35.0")
# B3-3 binance USDM 真实保证金率（来自交易所 fetch_balance）
# MMR ≥ 80% 距强平线 100% 仅 20%，1-2% 滑点即可穿透 — 立即熔断
MAX_BINANCE_MMR_PCT = Decimal("80.0")


@dataclass(frozen=True)
class BreakerDecision:
    allow: bool
    reason: str | None = None
    halt_metric: str | None = None
    # "daily_dd" | "weekly_dd" | "margin" | "ex_conc" | "sym_conc" |
    # "binance_mmr" | "data_unavailable"


# B3-1 全局 app_state 引用 — main.py lifespan 启动时调 set_app_state(app.state)
# 让 check_circuit_breakers 能读 reconciler/adapters，从而正确算 daily_dd 分母
_app_state_holder: dict[str, Any] = {"state": None}


def set_app_state(app_state: Any) -> None:
    """注入 FastAPI app.state 让熔断器能读 reconciler / adapters。

    main.py lifespan 启动时调用一次。"""
    _app_state_holder["state"] = app_state


async def check_circuit_breakers(
    strategy_label: str = "",
    app_state: Any = None,
) -> BreakerDecision:
    """实时检查账户级硬红线。

    Parameters
    ----------
    strategy_label:
        触发熔断的策略名（日志用）。
    app_state:
        FastAPI app.state — 用于读 reconciler / adapters。强烈建议传入；
        若不传则 daily_dd 分母退化到 ``_initial_capital + net_pnl``，与 UI
        显示不一致（B3-1）。

    Behavior
    --------
    - **fail-closed**：DB / hub 不可用时返回 `allow=False`（B3-2）。
      之前 fail-open 让"系统监控挂了反而继续开仓"成为反风控；
      DB 不可用本身就是系统级故障，应停手。
    - 触发任一硬红线返回 `allow=False`。
    """
    # 优先用显式参数，其次 fallback 到 module-level holder（lifespan 注入）
    state = app_state if app_state is not None else _app_state_holder.get("state")

    # 测试环境 escape：lifespan 未调 set_app_state 时（None holder），熔断器禁用
    # 让单测不必逐个 mock 这个全局检查
    if state is None:
        return BreakerDecision(allow=True)

    # P1-2: 通过 10s TTL cache 读 summary（三策略 + 前端轮询共享，免重复 11 query × N）
    summary = await _get_cached_summary(state)
    if summary is None:
        # B3-2 fail-closed：DB 故障即停手。监控故障不该等同于"绿灯继续开仓"。
        logger.error(
            "circuit_breaker_data_unavailable_fail_closed",
            strategy=strategy_label,
        )
        return BreakerDecision(
            allow=False,
            reason="data_unavailable",
            halt_metric="data_unavailable",
        )

    decision = _evaluate(summary, strategy_label)
    if not decision.allow:
        return decision

    # B3-3 binance USDM 真实 MMR 监控（fail-closed 兜底）
    # P1-2 重构后 adapters 不再在本 scope，重新从 state 取
    state_adapters = getattr(state, "adapters", None) if state else None
    mmr_decision = await _check_binance_mmr(state_adapters, strategy_label)
    return mmr_decision if mmr_decision is not None else decision


# P1-1: halt 告警去重 — 同 metric 5min 内不重复发 Telegram
_recent_alerts: dict[str, datetime] = {}
_ALERT_DEDUP_S = 300

# P1-2: get_summary 10s TTL 缓存 — 三策略 + 前端轮询共享
# 旧：每个 paper tick 都跑 11 次 DB query × 3 策略 × 60s = 33 query/min
# 新：10s TTL → 6 query/min (假设每 10s 一次唤醒，共享 cache)
_summary_cache: dict[str, Any] = {"data": None, "ts": 0.0}
_SUMMARY_CACHE_TTL_S = 10.0


async def _get_cached_summary(state: Any) -> dict[str, Any] | None:
    """读取共享 summary cache（10s TTL）；miss 则获取并缓存。"""
    import time as _time  # noqa: PLC0415
    now = _time.monotonic()
    if (
        _summary_cache["data"] is not None
        and (now - _summary_cache["ts"]) < _SUMMARY_CACHE_TTL_S
    ):
        return _summary_cache["data"]  # type: ignore[return-value]

    reconciler = getattr(state, "balance_reconciler", None) if state else None
    adapters = getattr(state, "adapters", None) if state else None
    try:
        from app.services.dashboard_service import get_summary  # noqa: PLC0415
        from app.core.database import get_session  # noqa: PLC0415
        async with get_session() as session:
            data = await get_summary(
                session, adapters=adapters, reconciler=reconciler,
            )
        _summary_cache["data"] = data
        _summary_cache["ts"] = now
        return data
    except Exception as exc:
        logger.error("summary_fetch_failed", error=str(exc)[:120])
        return None


def _maybe_telegram_critical(metric: str, strategy: str, reason: str) -> None:
    """halt 触发 → Telegram critical 告警（去重避免刷屏）。"""
    from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
    now = _dt.now(_tz.utc)
    last = _recent_alerts.get(metric)
    if last is not None and (now - last).total_seconds() < _ALERT_DEDUP_S:
        return  # 去重窗口内不重复发
    _recent_alerts[metric] = now
    try:
        from app.notifications import notify_reconcile_alert  # noqa: PLC0415
        notify_reconcile_alert(
            alert_type=f"circuit_breaker_{metric}",
            severity="critical",
            exchange="*",
            symbol=strategy,
            explanation=f"账户级硬红线触发熔断：{reason}（已阻断所有新开仓）",
        )
    except Exception:
        logger.debug("circuit_breaker_telegram_failed", metric=metric)


def _evaluate(summary: dict[str, Any], strategy_label: str) -> BreakerDecision:
    """纯函数评估（便于单测）。"""
    def _to_decimal(key: str) -> Decimal:
        try:
            return Decimal(str(summary.get(key) or 0))
        except Exception:
            return Decimal("0")

    daily_dd = _to_decimal("daily_drawdown_pct")
    weekly_dd = _to_decimal("weekly_dd_pct")
    margin = _to_decimal("margin_usage_pct")
    ex_conc = _to_decimal("max_exchange_concentration_pct")
    sym_conc = _to_decimal("max_symbol_concentration_pct")

    # 边缘 case：账户只在单一交易所有余额时，ex_concentration 必然 100%
    # 这不是风险（结构性事实），跳过 ex_conc 检查
    per_ex = summary.get("equity_by_exchange") or {}
    try:
        active_exchanges = sum(
            1 for v in per_ex.values() if Decimal(str(v or 0)) > 0
        )
    except Exception:
        active_exchanges = 0
    skip_ex_conc = active_exchanges < 2

    # 红线触发顺序：daily DD > weekly DD > margin > 集中度
    if daily_dd <= DAILY_DD_HALT_PCT:
        msg = f"daily_dd {daily_dd}% <= {DAILY_DD_HALT_PCT}%"
        logger.error("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        _maybe_telegram_critical("daily_dd", strategy_label, msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="daily_dd")
    if weekly_dd <= WEEKLY_DD_HALT_PCT:
        msg = f"weekly_dd {weekly_dd}% <= {WEEKLY_DD_HALT_PCT}%"
        logger.error("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        _maybe_telegram_critical("weekly_dd", strategy_label, msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="weekly_dd")
    if margin >= MIN_MARGIN_USAGE_PCT:
        msg = f"margin_usage {margin}% >= {MIN_MARGIN_USAGE_PCT}%"
        logger.error("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        _maybe_telegram_critical("margin", strategy_label, msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="margin")
    if not skip_ex_conc and ex_conc >= MAX_EXCHANGE_CONCENTRATION_PCT:
        msg = f"exchange_concentration {ex_conc}% >= {MAX_EXCHANGE_CONCENTRATION_PCT}%"
        logger.error("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        _maybe_telegram_critical("ex_conc", strategy_label, msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="ex_conc")
    if sym_conc >= MAX_SYMBOL_CONCENTRATION_PCT:
        msg = f"symbol_concentration {sym_conc}% >= {MAX_SYMBOL_CONCENTRATION_PCT}%"
        logger.error("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        _maybe_telegram_critical("sym_conc", strategy_label, msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="sym_conc")

    return BreakerDecision(allow=True)


async def _check_binance_mmr(
    adapters: Any, strategy_label: str,
) -> BreakerDecision | None:
    """B3-3 binance USDM 真实保证金率检查。

    DB 口径的 margin_used 与交易所内部 maintenance_margin_ratio 不一致：
    DB 只算"开仓时记录的保证金"，但市价波动会让交易所内部 MMR 升高，
    达到 100% 即强平。本函数读 binance fetch_balance 的真实状态。

    Returns
    -------
    None 表示通过（无适配器或 MMR 健康），否则返回阻断 BreakerDecision。
    """
    if not adapters:
        return None
    binance = adapters.get("binance") or adapters.get("binanceusdm")
    if binance is None:
        return None
    if not getattr(binance, "_api_key", ""):
        return None
    try:
        from app.exchanges.models import InstrumentType  # noqa: PLC0415
        perp_client = binance._clients.get(InstrumentType.PERPETUAL)
        if perp_client is None:
            return None
        raw = await perp_client.fetch_balance()
        # binance USDM totalMaintMargin / totalMarginBalance ≈ MMR
        info = (raw or {}).get("info", {}) or {}
        maint_margin = Decimal(str(info.get("totalMaintMargin") or 0))
        margin_balance = Decimal(str(info.get("totalMarginBalance") or 0))
        if margin_balance <= 0:
            return None
        mmr_pct = maint_margin / margin_balance * Decimal("100")
        if mmr_pct >= MAX_BINANCE_MMR_PCT:
            msg = f"binance_mmr {mmr_pct.quantize(Decimal('0.1'))}% >= {MAX_BINANCE_MMR_PCT}% (强平线临近)"
            logger.error(
                "circuit_breaker_halt_binance_mmr",
                strategy=strategy_label, mmr_pct=str(mmr_pct), reason=msg,
            )
            _maybe_telegram_critical("binance_mmr", strategy_label, msg)
            return BreakerDecision(allow=False, reason=msg, halt_metric="binance_mmr")
    except Exception as exc:
        logger.warning(
            "circuit_breaker_binance_mmr_check_failed",
            strategy=strategy_label, error=str(exc)[:120],
        )
    return None
