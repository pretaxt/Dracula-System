"""Positions 请求 / 响应 schema。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


def _decode_position_notes(notes: str) -> tuple[str, dict | None]:
    """从 PositionRecord.notes 解出 (symbol, meta_dict)。

    spot_perp_basis D.1+ 把 entry/close 成交价等编进 ``"BTC/USDT\\n{json}"``；
    funding_rate 等其他策略仅写 symbol（无 JSON）。统一在此解码。
    """
    if not notes:
        return "", None
    if "\n" not in notes:
        return notes, None
    head, _, tail = notes.partition("\n")
    try:
        import json as _json
        meta = _json.loads(tail)
        return head, meta if isinstance(meta, dict) else None
    except (ValueError, TypeError):
        return head, None


class LegOut(BaseModel):
    """单条 leg 静态 + 实时数据快照（用于持仓详情页 leg-level 视图）。"""
    exchange: str
    side: str                          # "long" | "short" | "buy" | "sell"
    instrument_type: str               # "perpetual" | "spot"
    symbol: str
    size: str
    entry_price: str
    # --- 实时数据（来自 MarketDataHub，可能为 None 表示未缓存）---
    current_price: str | None = None
    current_funding_rate: str | None = None        # 例如 "0.0001" = 0.01%
    current_apr_pct: str | None = None             # 折算年化（rate × periods/year × 100）
    next_funding_time_ms: int | None = None        # 下次结算 unix ms
    funding_interval_hours: int | None = None      # 8 / 4 / 1


class PositionOut(BaseModel):
    uuid: str
    symbol: str
    strategy_instance: str
    status: str
    notional_usd: str
    target_apr_pct: str | None
    unrealized_pnl: str
    realized_pnl: str
    funding_received: str
    fees_paid: str
    opened_at: datetime | None
    closed_at: datetime | None
    exit_reason: str | None
    days_held: str
    meta: dict[str, Any] | None = None  # spot_perp 解码后的 entry/close 元数据
    legs: list[LegOut] = []             # 跨所策略（#02 / #04）的双腿明细
    # --- 跨所策略派生指标（仅 #02 perp_basis 有意义，其余为 None）---
    current_diff_apr_pct: str | None = None     # SHORT.apr - LONG.apr 实时差
    current_price_divergence_pct: str | None = None  # |long_px - short_px| / mid * 100

    @classmethod
    def from_record(cls, rec, days_held: Decimal) -> "PositionOut":
        symbol, meta = _decode_position_notes(rec.notes or "")
        return cls(
            uuid=rec.uuid,
            symbol=symbol,
            strategy_instance=rec.strategy_instance,
            status=rec.status,
            notional_usd=str(rec.notional_usd),
            target_apr_pct=str(rec.target_apr_pct) if rec.target_apr_pct else None,
            unrealized_pnl=str(rec.unrealized_pnl),
            realized_pnl=str(rec.realized_pnl),
            funding_received=str(rec.funding_received),
            fees_paid=str(rec.fees_paid),
            opened_at=rec.opened_at,
            closed_at=rec.closed_at,
            exit_reason=rec.exit_reason,
            days_held=str(round(days_held, 4)),
            meta=meta,
        )


class PositionListMeta(BaseModel):
    page: int
    page_size: int
    total: int


class PositionListResponse(BaseModel):
    data: list[PositionOut]
    meta: PositionListMeta


class CloseRequest(BaseModel):
    reason: str = "manual"
    confirm: bool = False


class CloseResponse(BaseModel):
    uuid: str
    status: str
    message: str
