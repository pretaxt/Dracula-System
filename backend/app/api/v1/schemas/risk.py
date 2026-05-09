"""Risk 请求 / 响应 schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RiskLimitsOut(BaseModel):
    max_positions: int
    stop_loss_pct: str
    max_hold_hours: str
    min_apr_pct: str
    max_total_notional_usd: str
    scan_threshold_apr_pct: str = "0"   # 0 = 回退用 min_apr_pct


class RiskLimitsPatch(BaseModel):
    max_positions: int | None = None
    stop_loss_pct: str | None = None
    max_hold_hours: str | None = None
    min_apr_pct: str | None = None
    max_total_notional_usd: str | None = None
    scan_threshold_apr_pct: str | None = None
    confirm_widening: bool = False


class RiskEventOut(BaseModel):
    time: datetime
    tier: str
    event: str
    trigger: str
    value: str
    action: str
    auto_recovered: bool


class RiskEventsResponse(BaseModel):
    data: list[RiskEventOut]
    total: int
    days: int
