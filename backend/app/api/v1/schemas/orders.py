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
    price: str
    status: str
    position_uuid: str


class OrdersResponse(BaseModel):
    data: list[OrderOut]
    total: int
