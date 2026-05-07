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
        return {"name": name, "status": "unconfigured", "ping_ms": None}

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
        return {"name": name, "status": "unconfigured", "ping_ms": None}

    start = time.perf_counter()
    try:
        await asyncio.wait_for(client.fetch_time(), timeout=_PING_TIMEOUT_S)
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
        logger.debug("exchange_ping_failed", exchange=name, error=str(e))
        return {"name": name, "status": "critical", "ping_ms": None}

    ping_ms = int((time.perf_counter() - start) * 1000)
    if ping_ms <= _PING_OK_MS:
        status = "active"
    elif ping_ms <= _PING_WARN_MS:
        status = "warn"
    else:
        status = "critical"
    return {"name": name, "status": status, "ping_ms": ping_ms}


# ---------------------------------------------------------------------------
# 系统活动 (从 PositionRecord 真实衍生)
# ---------------------------------------------------------------------------


async def get_recent_activity(session: AsyncSession, limit: int = 10) -> list[dict]:
    """最近 N 条真实活动:开仓 / 平仓 / 资金费入账。来源全是 PositionRecord。"""
    stmt = (
        select(PositionRecord)
        .order_by(desc(PositionRecord.opened_at))
        .limit(limit * 2)
    )
    rows = (await session.execute(stmt)).scalars().all()
    now = datetime.now(timezone.utc)
    activities: list[dict] = []

    for r in rows:
        symbol = (r.notes or "").split("@")[0].strip() or "?"

        if r.closed_at:
            net = (r.realized_pnl or Decimal("0")) + (r.funding_received or Decimal("0"))
            icon = "x" if (r.exit_reason and "stop" in (r.exit_reason or "")) else "check"
            activities.append({
                "icon": icon,
                "text": _format_close_text(symbol, r.exit_reason or "manual", float(net)),
                "time": _format_time(r.closed_at, now),
                "_sort_at": r.closed_at,
            })

        if r.opened_at:
            activities.append({
                "icon": "check" if r.status == "open" else "up",
                "text": _format_open_text(
                    symbol, float(r.notional_usd or 0), float(r.target_apr_pct or 0)
                ),
                "time": _format_time(r.opened_at, now),
                "_sort_at": r.opened_at,
            })

        if (r.funding_received or Decimal("0")) > 0:
            activities.append({
                "icon": "up",
                "text": f"资金费率结算 · {symbol} +${float(r.funding_received):.2f}",
                "time": _format_time(r.opened_at or now, now),
                "_sort_at": r.opened_at or now,
            })

    activities.sort(key=lambda a: a["_sort_at"] or now, reverse=True)
    out = activities[:limit]
    for a in out:
        a.pop("_sort_at", None)
    return out


def _format_open_text(symbol: str, notional: float, apr: float) -> str:
    return f"建仓成功 · {symbol} · APR {apr:.2f}% · 仓位 ${notional:.0f}"


def _format_close_text(symbol: str, exit_reason: str, net_pnl: float) -> str:
    sign = "+" if net_pnl >= 0 else "-"
    return f"平仓 · {symbol} · 触发: {exit_reason} · {sign}${abs(net_pnl):.2f}"


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
        symbol = (r.notes or "").split("@")[0].strip() or "?"
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
    pnl_str = f"{'+' if realized_pnl >= 0 else ''}${float(realized_pnl):.2f}"
    if reason == "stop_loss":
        return ("TIER 2", "止损触发", f"{symbol} 止损线", pnl_str, "stopped_out", True)
    if reason == "funding_reversal":
        return ("TIER 1", "资金费率反转", f"{symbol} funding 转负", pnl_str, "auto_closed", True)
    if reason == "max_hold":
        return ("TIER 1", "持仓超时平仓", f"{symbol} max hold", pnl_str, "auto_closed", True)
    if reason == "liquidation":
        return ("TIER 3", "强平", f"{symbol} 保证金不足", pnl_str, "force_closed", False)
    if reason in {"manual", "manual_close"}:
        return ("TIER 1", "手动平仓", f"{symbol} 操作员", pnl_str, "manual", True)
    return ("TIER 1", f"平仓 ({reason})", symbol, pnl_str, reason, True)
