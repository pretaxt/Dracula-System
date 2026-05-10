"""全局风控熔断器 — 在策略入场前 enforcement 账户级硬红线。

之前的设计漏洞：daily/weekly drawdown 红线只在 dashboard 显示，没有阻断 runner
开仓。账户已亏 -3%，runner 仍每 60s 开新仓 — 这是必须修复的 P0 风控漏洞。

本模块提供单一入口 `check_circuit_breakers()`，所有 paper_trading session 在
_open_position 前必须调用：触发任一硬红线时返回 `(False, reason)`，阻断新开仓。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 硬红线阈值（与「锁定红线·账户级」UI 显示对齐）
# ---------------------------------------------------------------------------

DAILY_DD_HALT_PCT = Decimal("-3.0")      # 单日 PnL ≤ -3% 即熔断
WEEKLY_DD_HALT_PCT = Decimal("-8.0")     # 周 PnL ≤ -8% 即熔断
MIN_MARGIN_USAGE_PCT = Decimal("50.0")   # 保证金占用 ≥ 50% 即熔断
MAX_EXCHANGE_CONCENTRATION_PCT = Decimal("50.0")
MAX_SYMBOL_CONCENTRATION_PCT = Decimal("20.0")


@dataclass(frozen=True)
class BreakerDecision:
    allow: bool
    reason: str | None = None
    halt_metric: str | None = None  # "daily_dd" | "weekly_dd" | "margin" | "ex_conc" | "sym_conc"


async def check_circuit_breakers(strategy_label: str = "") -> BreakerDecision:
    """实时检查账户级硬红线。

    数据源：dashboard_service.compute_summary（同 UI 锁定红线卡数据源）。
    单点失败时（DB / hub 不可用）返回 allow=True 不阻断 — fail-open 设计：
    监控系统挂了不应连带停所有策略。但记 warning 让 oncall 关注。
    """
    try:
        from app.services.dashboard_service import get_summary  # noqa: PLC0415
        from app.core.database import get_session  # noqa: PLC0415
        async with get_session() as session:
            summary = await get_summary(session)
    except Exception as exc:
        logger.warning(
            "circuit_breaker_compute_failed_fail_open",
            strategy=strategy_label, error=str(exc)[:120],
        )
        return BreakerDecision(allow=True)

    return _evaluate(summary, strategy_label)


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

    # 红线触发顺序：daily DD > weekly DD > margin > 集中度
    if daily_dd <= DAILY_DD_HALT_PCT:
        msg = f"daily_dd {daily_dd}% <= {DAILY_DD_HALT_PCT}%"
        logger.warning("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="daily_dd")
    if weekly_dd <= WEEKLY_DD_HALT_PCT:
        msg = f"weekly_dd {weekly_dd}% <= {WEEKLY_DD_HALT_PCT}%"
        logger.warning("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="weekly_dd")
    if margin >= MIN_MARGIN_USAGE_PCT:
        msg = f"margin_usage {margin}% >= {MIN_MARGIN_USAGE_PCT}%"
        logger.warning("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="margin")
    if ex_conc >= MAX_EXCHANGE_CONCENTRATION_PCT:
        msg = f"exchange_concentration {ex_conc}% >= {MAX_EXCHANGE_CONCENTRATION_PCT}%"
        logger.warning("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="ex_conc")
    if sym_conc >= MAX_SYMBOL_CONCENTRATION_PCT:
        msg = f"symbol_concentration {sym_conc}% >= {MAX_SYMBOL_CONCENTRATION_PCT}%"
        logger.warning("circuit_breaker_halt", strategy=strategy_label, reason=msg)
        return BreakerDecision(allow=False, reason=msg, halt_metric="sym_conc")

    return BreakerDecision(allow=True)
