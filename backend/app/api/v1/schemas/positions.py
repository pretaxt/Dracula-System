"""Positions 请求 / 响应 schema。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


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

    @classmethod
    def from_record(cls, rec, days_held: Decimal) -> "PositionOut":
        return cls(
            uuid=rec.uuid,
            symbol=rec.notes or "",
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
