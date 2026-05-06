"""Risk 请求 / 响应 schema。"""
from __future__ import annotations

from pydantic import BaseModel


class RiskLimitsOut(BaseModel):
    max_positions: int
    stop_loss_pct: str
    max_hold_hours: str
    min_apr_pct: str
    max_total_notional_usd: str


class RiskLimitsPatch(BaseModel):
    max_positions: int | None = None
    stop_loss_pct: str | None = None
    max_hold_hours: str | None = None
    min_apr_pct: str | None = None
    max_total_notional_usd: str | None = None
    confirm_widening: bool = False
