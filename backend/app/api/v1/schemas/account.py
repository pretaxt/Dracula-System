"""Account 响应 schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AccountBalance(BaseModel):
    total_equity_usd: str
    available_usd: str
    locked_usd: str
    positions_notional_usd: str
    realized_pnl_usd: str
    unrealized_pnl_usd: str
    currency: str
    updated_at: datetime
