"""Market 路由响应 schema — 行情中心 ticker。"""
from __future__ import annotations

from pydantic import BaseModel


class MarketTicker(BaseModel):
    symbol: str
    exchange: str
    last: str
    change_24h_pct: str
    volume_24h_usd: str
    high_24h: str = "0"
    low_24h: str = "0"
    funding_rate: str
    funding_rate_pct: str
    next_funding_time_ms: int
    ts: int


class MarketTickersResponse(BaseModel):
    data: list[MarketTicker]
    snapshot_at: int


class KlineBar(BaseModel):
    time: int
    open: str
    high: str
    low: str
    close: str
    volume: str


class KlinesResponse(BaseModel):
    symbol: str
    interval: str
    data: list[KlineBar]


class OrderbookResponse(BaseModel):
    symbol: str
    bids: list[list[str]]   # [[price, qty], ...]
    asks: list[list[str]]
    ts: int
