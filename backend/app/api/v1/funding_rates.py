"""FundingRates 路由。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.funding_rates import (
    FundingHistoryMeta,
    FundingHistoryOut,
    FundingHistoryResponse,
    OpportunitiesResponse,
    OpportunityOut,
)
from app.services.funding_service import get_funding_history, get_opportunities

router = APIRouter(prefix="/funding-rates", tags=["funding-rates"])


@router.get("/opportunities", response_model=OpportunitiesResponse)
async def opportunities(_: CurrentUser) -> OpportunitiesResponse:
    data, snapshot_at = await get_opportunities()
    items = [
        OpportunityOut(
            exchange=d.get("exchange", ""),
            symbol=d.get("symbol", ""),
            funding_rate=str(d.get("funding_rate", "0")),
            apr_pct=str(d.get("apr_pct", "0")),
            next_funding_time=d.get("next_funding_time"),
            instrument_type=d.get("instrument_type", "PERPETUAL"),
            history_positive=d.get("history_positive"),
        )
        for d in data
    ]
    items.sort(key=lambda x: float(x.apr_pct), reverse=True)
    return OpportunitiesResponse(data=items, snapshot_at=snapshot_at)


@router.get("/history", response_model=FundingHistoryResponse)
async def history(
    _: CurrentUser,
    db: DbSession,
    exchange: str | None = None,
    symbol: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    page: int = 1,
    page_size: int = 100,
) -> FundingHistoryResponse:
    rows, total = await get_funding_history(
        db,
        exchange=exchange,
        symbol=symbol,
        from_dt=from_dt,
        to_dt=to_dt,
        page=page,
        page_size=page_size,
    )
    data = [
        FundingHistoryOut(
            exchange=r.exchange,
            symbol=r.symbol,
            funding_rate=str(r.funding_rate),
            apr_pct=str(r.apr_pct),
            recorded_at=r.time,
        )
        for r in rows
    ]
    return FundingHistoryResponse(
        data=data,
        meta=FundingHistoryMeta(page=page, page_size=page_size, total=total),
    )
