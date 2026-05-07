"""Dashboard 响应 schema。"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class PnlPoint(BaseModel):
    date: date
    net_pnl_usd: str


class DashboardSummary(BaseModel):
    net_pnl_usd: str
    realized_pnl_usd: str
    unrealized_pnl_usd: str
    today_funding_usd: str
    monthly_pnl_usd: str
    daily_drawdown_pct: str
    total_equity_usd: str
    open_positions: int
    avg_apr_pct: str
    pnl_series_30d: list[PnlPoint]
