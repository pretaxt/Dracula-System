"""System 路由 — 交易所实时健康 / 活动流。"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.system import (
    ActivityOut,
    ActivityResponse,
    ExchangeHealthOut,
    ExchangeHealthResponse,
)
from app.services.system_service import get_exchange_health, get_recent_activity

router = APIRouter(prefix="/system", tags=["system"])


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
