"""Market 路由 — 实时行情批量 ticker + K 线。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser
from app.api.v1.schemas.market import (
    KlineBar,
    KlinesResponse,
    MarketTicker,
    MarketTickersResponse,
    OrderbookResponse,
)
from app.services.market_service import get_klines, get_orderbook, get_tickers

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/tickers", response_model=MarketTickersResponse)
async def tickers(
    _: CurrentUser,
    request: Request,
    symbols: str | None = Query(default=None, description="comma-separated bases, e.g. BTC,ETH"),
    exchange: str = Query(default="binance"),
) -> MarketTickersResponse:
    """批量 ticker + funding rate(实时拉)。"""
    adapters = getattr(request.app.state, "adapters", None) or {}
    pool = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else None
    )
    raw = await get_tickers(adapters, symbols=pool, exchange=exchange)
    return MarketTickersResponse(
        data=[MarketTicker(**r) for r in raw],
        snapshot_at=int(time.time() * 1000),
    )


@router.get("/klines", response_model=KlinesResponse)
async def klines(
    _: CurrentUser,
    request: Request,
    symbol: str = Query(..., description="base symbol, e.g. BTC"),
    interval: str = Query(default="1h", description="1m/5m/15m/30m/1h/2h/4h/1d/1w"),
    limit: int = Query(default=100, ge=10, le=500),
    exchange: str = Query(default="binance"),
) -> KlinesResponse:
    """USDM perp K 线 (OHLCV)."""
    adapters = getattr(request.app.state, "adapters", None) or {}
    raw = await get_klines(
        adapters, symbol=symbol, interval=interval, limit=limit, exchange=exchange
    )
    return KlinesResponse(
        symbol=symbol.upper(),
        interval=interval,
        data=[KlineBar(**r) for r in raw],
    )


@router.get("/orderbook", response_model=OrderbookResponse)
async def orderbook(
    _: CurrentUser,
    request: Request,
    symbol: str = Query(..., description="base symbol, e.g. BTC"),
    depth: int = Query(default=20, ge=5, le=50),
    exchange: str = Query(default="binance"),
) -> OrderbookResponse:
    """USDM perp 盘口深度."""
    adapters = getattr(request.app.state, "adapters", None) or {}
    raw = await get_orderbook(adapters, symbol=symbol, depth=depth, exchange=exchange)
    return OrderbookResponse(
        symbol=symbol.upper(),
        bids=raw.get("bids", []),
        asks=raw.get("asks", []),
        ts=raw.get("ts", 0),
    )
