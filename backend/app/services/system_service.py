"""系统级聚合服务 — 交易所健康 / 系统活动 / 风控事件衍生。

数据源:
  - app.state.adapters (CCXT 客户端,真实 fetch_time ping)
  - PositionRecord (历史事件衍生)
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.position import PositionRecord

logger = get_logger(__name__)

EXCHANGE_DISPLAY_NAMES: list[str] = [
    "binance",
    "bybit",
    "okx",
    "htx",
    "bitget",
    "hyperliquid",
]

_PING_OK_MS = 800
_PING_WARN_MS = 2000
_PING_TIMEOUT_S = 3.0


# ---------------------------------------------------------------------------
# 交易所健康 (实时 ping)
# ---------------------------------------------------------------------------


async def get_exchange_health(adapters: dict[str, Any] | None) -> list[dict]:
    """6 交易所健康状态 + 延迟。无 adapter 的返回 ``unconfigured``。"""
    adapters = adapters or {}
    tasks = [_probe_one(name, adapters.get(name)) for name in EXCHANGE_DISPLAY_NAMES]
    return await asyncio.gather(*tasks)


async def _probe_one(name: str, adapter: Any) -> dict:
    if adapter is None:
        return {
            "name": name, "status": "unconfigured", "ping_ms": None,
            "has_credentials": False,
        }

    # 区分 "市场公开数据可用" vs "鉴权 API 可用"
    has_credentials = bool(getattr(adapter, "_api_key", "") or "")

    client = None
    clients = getattr(adapter, "_clients", None)
    if clients:
        try:
            from app.exchanges.models import InstrumentType  # noqa: PLC0415

            client = (
                clients.get(InstrumentType.PERPETUAL)
                or clients.get(InstrumentType.SPOT)
                or next(iter(clients.values()), None)
            )
        except Exception:  # noqa: BLE001
            client = next(iter(clients.values()), None) if clients else None

    if client is None or not hasattr(client, "fetch_time"):
        return {
            "name": name, "status": "unconfigured", "ping_ms": None,
            "has_credentials": has_credentials,
        }

    start = time.perf_counter()
    try:
        await asyncio.wait_for(client.fetch_time(), timeout=_PING_TIMEOUT_S)
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
        logger.debug("exchange_ping_failed", exchange=name, error=str(e))
        return {
            "name": name, "status": "critical", "ping_ms": None,
            "has_credentials": has_credentials,
        }

    ping_ms = int((time.perf_counter() - start) * 1000)
    if ping_ms <= _PING_OK_MS:
        status = "active"
    elif ping_ms <= _PING_WARN_MS:
        status = "warn"
    else:
        status = "critical"
    # 行情可达但无 trading 凭据 → 余额同步不可用，UI 应提示用户配凭据
    if not has_credentials and status == "active":
        status = "no_credentials"
    return {
        "name": name, "status": status, "ping_ms": ping_ms,
        "has_credentials": has_credentials,
    }


# ---------------------------------------------------------------------------
# 系统活动 (从 PositionRecord 真实衍生)
# ---------------------------------------------------------------------------


def _format_exchanges(legs: list) -> str:
    """根据 legs 构造交易所标签：跨所 "OKX→HTX"，单所 "BINANCE"。"""
    if not legs:
        return ""
    longs = [l for l in legs if (l.side or "").lower() in ("buy", "long")]
    shorts = [l for l in legs if (l.side or "").lower() in ("sell", "short")]
    if longs and shorts:
        return f"{longs[0].exchange.upper()}→{shorts[0].exchange.upper()}"
    if legs:
        return " / ".join(sorted(set((l.exchange or "?").upper() for l in legs)))
    return ""


_STRATEGY_BADGE = {
    "funding_rate_main": "资金费率",
    "perp_basis_main": "跨所基差",
    "spot_perp_main": "期现",
    "price_spread_main": "价差",
    "cex_dex_main": "CEX-DEX",
}


def _format_strategy_badge(strategy_instance: str | None) -> str:
    """根据 strategy_instance 返回中文 Badge 字符串。"""
    if not strategy_instance:
        return ""
    return _STRATEGY_BADGE.get(strategy_instance, strategy_instance)


def _format_hold_time(hours: float | None) -> str:
    """将持仓小时数格式化为 "5h 12m" 或 "42m" 或 "3d 4h"。"""
    if hours is None or hours <= 0:
        return ""
    if hours < 1:
        return f"{int(hours * 60)}m"
    if hours < 24:
        h = int(hours)
        m = int((hours - h) * 60)
        return f"{h}h {m}m" if m else f"{h}h"
    d = int(hours // 24)
    h = int(hours - d * 24)
    return f"{d}d {h}h" if h else f"{d}d"


async def get_recent_activity(session: AsyncSession, limit: int = 10) -> list[dict]:
    """最近 N 条真实活动:开仓 / 平仓 / 资金费入账。来源全是 PositionRecord。

    扩展(2026-05-14)：加交易所信息 (long→short / 单所)，从 position_legs JOIN 获取。
    """
    from app.models.position import PositionLegRecord  # noqa: PLC0415

    stmt = (
        select(PositionRecord)
        .order_by(desc(PositionRecord.opened_at))
        .limit(limit * 2)
    )
    rows = (await session.execute(stmt)).scalars().all()

    # 一次性 JOIN legs（避免 N+1）
    pos_ids = [r.id for r in rows]
    legs_by_pos: dict[int, list] = {}
    if pos_ids:
        leg_stmt = select(PositionLegRecord).where(
            PositionLegRecord.position_id.in_(pos_ids)
        )
        leg_rows = (await session.execute(leg_stmt)).scalars().all()
        for leg in leg_rows:
            legs_by_pos.setdefault(leg.position_id, []).append(leg)

    now = datetime.now(timezone.utc)
    activities: list[dict] = []

    for r in rows:
        first_line = (r.notes or "").split("\n", 1)[0]
        symbol = first_line.split("@", 1)[0].strip() or "?"
        is_spot_perp = "spot_perp" in (r.strategy_instance or "")
        ex_label = _format_exchanges(legs_by_pos.get(r.id, []))
        strat_badge = _format_strategy_badge(r.strategy_instance)
        hold_h = None
        if r.closed_at and r.opened_at:
            hold_h = (r.closed_at - r.opened_at).total_seconds() / 3600

        if r.closed_at:
            realized = float(r.realized_pnl or 0)
            funding = float(r.funding_received or 0)
            fees = float(r.fees_paid or 0)
            net = realized + funding - fees
            # icon 语义化：盈利 'check'，亏损 'down'，止损类 'alert'
            if r.exit_reason and any(k in r.exit_reason for k in ("stop", "liq", "unwind")):
                icon = "alert"
            elif net > 0:
                icon = "check"
            else:
                icon = "down"
            activities.append({
                "icon": icon,
                "text": _format_close_text(
                    symbol, r.exit_reason or "manual", net,
                    ex_label, hold_h, strat_badge, realized, funding, fees,
                ),
                "time": _format_time(r.closed_at, now),
                "_sort_at": r.closed_at,
            })

        if r.opened_at:
            activities.append({
                "icon": "open" if r.status == "open" else "up",
                "text": _format_open_text(
                    symbol, float(r.notional_usd or 0),
                    float(r.target_apr_pct or 0), is_spot_perp, ex_label, strat_badge,
                ),
                "time": _format_time(r.opened_at, now),
                "_sort_at": r.opened_at,
            })

        if (r.funding_received or Decimal("0")) > 0:
            _funding_text = ""
            if strat_badge:
                _funding_text += f"[{strat_badge}] "
            _funding_text += f"资金费率结算 · {symbol}"
            if ex_label:
                _funding_text += f" [{ex_label}]"
            _funding_text += f" +${float(r.funding_received):.2f}"
            activities.append({
                "icon": "money",
                "text": _funding_text,
                "time": _format_time(r.opened_at or now, now),
                "_sort_at": r.opened_at or now,
            })

    activities.sort(key=lambda a: a["_sort_at"] or now, reverse=True)
    out = activities[:limit]
    for a in out:
        a.pop("_sort_at", None)
    return out


_EXIT_REASON_ZH = {
    "basis_convergence": "基差收敛",
    "max_hold": "持仓超时",
    "max_hold_time": "持仓超时",
    "manual": "手动平仓",
    "manual_close": "手动平仓",
    "manual_emergency": "紧急平仓",
    "stop_loss": "止损",
    "basis_stop": "基差止损",
    "liquidation": "强平",
    "perp_liq_risk": "强平兜底",
    "funding_reversal": "费率反转",
    "strategy": "策略退出",
    "diff_decay": "费差衰减",  # #02 perp_basis: diff_apr 衰减到 exit_diff_apr_pct
    "price_divergence": "价格脱钩",  # #02 perp_basis: 跨所价差超 stop_price_divergence_pct
    "risk_limit": "风控触发",
}


def _format_open_text(
    symbol: str, notional: float, target_pct: float, is_spot_perp: bool,
    ex_label: str = "", strat_badge: str = "",
) -> str:
    """spot_perp 显示基差 %; funding_rate/perp_basis 显示 APR/diff %。"""
    label = "基差" if is_spot_perp else "APR"
    sign = "+" if target_pct >= 0 else ""
    ex_part = f" [{ex_label}]" if ex_label else ""
    badge = f"[{strat_badge}] " if strat_badge else ""
    return f"{badge}建仓 · {symbol}{ex_part} · {label} {sign}{target_pct:.2f}% · 仓位 ${notional:.0f}"


def _format_close_text(
    symbol: str, exit_reason: str, net_pnl: float,
    ex_label: str = "", hold_h: float | None = None,
    strat_badge: str = "", realized: float = 0.0,
    funding: float = 0.0, fees: float = 0.0,
) -> str:
    sign = "+" if net_pnl >= 0 else "-"
    reason_zh = _EXIT_REASON_ZH.get(exit_reason, exit_reason)
    ex_part = f" [{ex_label}]" if ex_label else ""
    badge = f"[{strat_badge}] " if strat_badge else ""
    hold_part = f" · 持仓 {_format_hold_time(hold_h)}" if hold_h else ""
    # PnL 拆解(只在数值非 0 时显示)
    parts = []
    if realized != 0:
        parts.append(f"实现 {'+' if realized >= 0 else '-'}${abs(realized):.2f}")
    if funding > 0:
        parts.append(f"资费 +${funding:.2f}")
    if fees > 0:
        parts.append(f"费 -${fees:.2f}")
    breakdown = f" ({' · '.join(parts)})" if parts else ""
    return f"{badge}平仓 · {symbol}{ex_part} · {reason_zh} · {sign}${abs(net_pnl):.2f}{breakdown}{hold_part}"


def _format_time(at: datetime, now: datetime) -> str:
    delta = now - at
    seconds = int(delta.total_seconds())
    if seconds < 60:
        rel = f"{seconds} 秒前"
    elif seconds < 3600:
        rel = f"{seconds // 60} 分钟前"
    elif seconds < 86_400:
        rel = f"{seconds // 3600} 小时前"
    else:
        rel = f"{seconds // 86_400} 天前"
    utc = at.strftime("%H:%M:%S UTC")
    return f"{utc} · {rel}"


# ---------------------------------------------------------------------------
# 风控事件 (PositionRecord exit_reason + 即时 daily DD)
# ---------------------------------------------------------------------------


async def get_recent_risk_events(
    session: AsyncSession,
    days: int = 30,
    daily_dd_pct: Decimal | None = None,
) -> list[dict]:
    """从 PositionRecord 历史 + 即时 daily DD 衍生事件流。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (
        select(PositionRecord)
        .where(PositionRecord.closed_at.is_not(None))
        .where(PositionRecord.exit_reason.is_not(None))
        .where(PositionRecord.closed_at >= cutoff)
        .order_by(desc(PositionRecord.closed_at))
        .limit(50)
    )
    rows = (await session.execute(stmt)).scalars().all()

    events: list[dict] = []

    if daily_dd_pct is not None:
        dd_abs = abs(float(daily_dd_pct))
        if dd_abs >= 3.0:
            events.append({
                "time": datetime.now(timezone.utc),
                "tier": "TIER 3",
                "event": "单日回撤触及红线",
                "trigger": "daily DD",
                "value": f"{daily_dd_pct}% / -3.00%",
                "action": "halt_all_new_positions",
                "auto_recovered": False,
            })
        elif dd_abs >= 2.0:
            events.append({
                "time": datetime.now(timezone.utc),
                "tier": "TIER 2",
                "event": "单日回撤接近红线",
                "trigger": "daily DD",
                "value": f"{daily_dd_pct}% / -3.00%",
                "action": "throttle_position_size",
                "auto_recovered": False,
            })
        elif dd_abs >= 1.0:
            events.append({
                "time": datetime.now(timezone.utc),
                "tier": "TIER 1",
                "event": "单日回撤进入观察",
                "trigger": "daily DD",
                "value": f"{daily_dd_pct}% / -3.00%",
                "action": "watch_only",
                "auto_recovered": False,
            })

    for r in rows:
        # spot_perp D.1+ notes 末尾含 "\n{json}"，先按 \n 切再取首段；兼容旧 "@" 分隔
        first_line = (r.notes or "").split("\n", 1)[0]
        symbol = first_line.split("@", 1)[0].strip() or "?"
        reason = r.exit_reason or ""
        tier, event_name, trigger_text, value, action, auto = _classify_exit(
            reason, symbol, r.realized_pnl or Decimal("0")
        )
        events.append({
            "time": r.closed_at,
            "tier": tier,
            "event": event_name,
            "trigger": trigger_text,
            "value": value,
            "action": action,
            "auto_recovered": auto,
        })

    return events


def _classify_exit(
    reason: str, symbol: str, realized_pnl: Decimal
) -> tuple[str, str, str, str, str, bool]:
    """根据 exit_reason 生成友好中文文案 (tier, event, trigger, value, action, auto)。"""
    pnl_str = f"{'+' if realized_pnl >= 0 else ''}${float(realized_pnl):.2f}"
    if reason == "stop_loss":
        return ("TIER 2", "止损触发", f"{symbol} 跌破止损线", pnl_str, "stopped_out", True)
    if reason == "basis_stop":
        return ("TIER 2", "基差止损", f"{symbol} 基差扩大触发止损", pnl_str, "stopped_out", True)
    if reason == "funding_reversal":
        return ("TIER 1", "资金费率反转", f"{symbol} 费率转负", pnl_str, "auto_closed", True)
    if reason == "max_hold":
        return ("TIER 1", "持仓超时平仓", f"{symbol} 达到最长持仓", pnl_str, "auto_closed", True)
    if reason == "basis_convergence":
        return ("TIER 1", "基差收敛平仓", f"{symbol} 基差回归", pnl_str, "auto_closed", True)
    if reason in {"liquidation", "perp_liq_risk"}:
        return ("TIER 3", "强平", f"{symbol} 保证金不足", pnl_str, "force_closed", False)
    if reason in {"manual", "manual_close"}:
        return ("TIER 1", "手动平仓", f"{symbol} 用户操作", pnl_str, "manual", True)
    return ("TIER 1", f"平仓 ({reason})", symbol, pnl_str, reason, True)
