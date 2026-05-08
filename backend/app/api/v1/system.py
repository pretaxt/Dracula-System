"""System 路由 — 交易所实时健康 / 活动流 / 扫描宇宙。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.system import (
    ActivityOut,
    ActivityResponse,
    ExchangeHealthOut,
    ExchangeHealthResponse,
)
from app.services.system_service import get_exchange_health, get_recent_activity

router = APIRouter(prefix="/system", tags=["system"])


class SymbolsResponse(BaseModel):
    total: int
    symbols: list[str]  # ["BTC/USDT", "ETH/USDT", ...]


@router.get("/symbols", response_model=SymbolsResponse)
async def list_symbols(_: CurrentUser, request: Request) -> SymbolsResponse:
    """返回当前策略扫描的全部 USDT 永续标的（按字母序）。

    数据源：main.py 启动时从 binance + okx 适配器聚合，存于 app.state.symbols。
    """
    syms = getattr(request.app.state, "symbols", None) or []
    formatted = sorted({f"{s.base}/{s.quote}" for s in syms})
    return SymbolsResponse(total=len(formatted), symbols=formatted)


@router.get("/exchanges/health", response_model=ExchangeHealthResponse)
async def exchanges_health(_: CurrentUser, request: Request) -> ExchangeHealthResponse:
    """6 交易所实时延迟 ping(适配器存在的真实测,缺失的返回 unconfigured)。"""
    adapters = getattr(request.app.state, "adapters", None) or {}
    raw = await get_exchange_health(adapters)
    return ExchangeHealthResponse(data=[ExchangeHealthOut(**r) for r in raw])


@router.get("/activity", response_model=ActivityResponse)
async def activity(
    _: CurrentUser,
    db: DbSession,
    limit: int = Query(default=10, ge=1, le=50),
) -> ActivityResponse:
    """最近活动:开仓 / 平仓 / 资金费入账(从 PositionRecord 真实衍生)。"""
    raw = await get_recent_activity(db, limit=limit)
    return ActivityResponse(data=[ActivityOut(**r) for r in raw])
