"""Dashboard 路由 — GET /dashboard/summary。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.dashboard import DashboardSummary, PnlPoint, StrategyPerf
from app.services.dashboard_service import get_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def summary(_: CurrentUser, db: DbSession, request: Request) -> DashboardSummary:
    adapters = getattr(request.app.state, "adapters", None)
    data = await get_summary(db, adapters=adapters)
    return DashboardSummary(
        net_pnl_usd=data["net_pnl_usd"],
        realized_pnl_usd=data["realized_pnl_usd"],
        unrealized_pnl_usd=data["unrealized_pnl_usd"],
        today_funding_usd=data["today_funding_usd"],
        monthly_pnl_usd=data["monthly_pnl_usd"],
        daily_drawdown_pct=data["daily_drawdown_pct"],
        weekly_dd_pct=data["weekly_dd_pct"],
        margin_usage_pct=data["margin_usage_pct"],
        api_error_rate_5m_pct=data["api_error_rate_5m_pct"],
        ws_stability_pct=data["ws_stability_pct"],
        total_equity_usd=data["total_equity_usd"],
        open_positions=data["open_positions"],
        avg_apr_pct=data["avg_apr_pct"],
        pnl_series_30d=[PnlPoint(**p) for p in data["pnl_series_30d"]],
        strategy_performance=[
            StrategyPerf(**s) for s in data.get("strategy_performance", [])
        ],
        equity_by_exchange=data.get("equity_by_exchange", {}),
        sharpe_30d=data.get("sharpe_30d", "0"),
        max_exchange_concentration_pct=data.get("max_exchange_concentration_pct", "0"),
        max_symbol_concentration_pct=data.get("max_symbol_concentration_pct", "0"),
        api_latency_p95_ms=data.get("api_latency_p95_ms", "0"),
        scan_perf=data.get("scan_perf", {}),
        ccxt_health=data.get("ccxt_health", {}),
    )
