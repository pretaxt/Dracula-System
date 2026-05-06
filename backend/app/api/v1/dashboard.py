"""Dashboard 路由 — GET /dashboard/summary。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.dashboard import DashboardSummary, PnlPoint
from app.services.dashboard_service import get_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def summary(_: CurrentUser, db: DbSession) -> DashboardSummary:
    data = await get_summary(db)
    return DashboardSummary(
        net_pnl_usd=data["net_pnl_usd"],
        realized_pnl_usd=data["realized_pnl_usd"],
        unrealized_pnl_usd=data["unrealized_pnl_usd"],
        today_funding_usd=data["today_funding_usd"],
        open_positions=data["open_positions"],
        avg_apr_pct=data["avg_apr_pct"],
        pnl_series_30d=[PnlPoint(**p) for p in data["pnl_series_30d"]],
    )
