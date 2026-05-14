"""Orders 响应 schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OrderOut(BaseModel):
    time: datetime
    exchange: str
    symbol: str
    order_type: str
    side: str
    amount: str
    pnl: str          # 平仓时为已实现盈亏（带符号），开仓时为 "—"
    status: str
    position_uuid: str
    exit_reason: str  # 平仓原因（diff_decay/price_divergence/manual 等）；开仓时为 "—"


class OrdersResponse(BaseModel):
    data: list[OrderOut]
    total: int
