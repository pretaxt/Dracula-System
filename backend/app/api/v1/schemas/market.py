"""Market 路由响应 schema — 行情中心 ticker。"""
from __future__ import annotations

from pydantic import BaseModel


class MarketTicker(BaseModel):
    symbol: str
    exchange: str
    last: str
    change_24h_pct: str
    volume_24h_usd: str
    funding_rate: str
    funding_rate_pct: str
    next_funding_time_ms: int
    ts: int


class MarketTickersResponse(BaseModel):
    data: list[MarketTicker]
    snapshot_at: int
