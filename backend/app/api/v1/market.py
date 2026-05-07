"""Market 路由 — 实时行情批量 ticker。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser
from app.api.v1.schemas.market import MarketTicker, MarketTickersResponse
from app.services.market_service import get_tickers

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
