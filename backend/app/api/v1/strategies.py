"""Strategies 路由。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.api.v1.schemas.strategies import (
    ConfigPatchRequest,
    SpotPerpOpportunitiesResponse,
    SpotPerpOpportunityOut,
    StrategyActionResponse,
    StrategyConfig,
    StrategyStatusResponse,
)
from app.services.strategy_control import patch_strategy_config, start_paper, stop_paper

router = APIRouter(prefix="/strategies", tags=["strategies"])

# 已实现真实控制的策略 (funding_rate 是 P0 主力)
_LIVE_STRATEGIES = {"funding-rate"}

# 12 策略保留列表 (2026-05-07 决策, 砍掉 #8 #11 #12 #15 #17)
_VALID_STRATEGY_IDS = {
    "funding-rate", "spot-perp", "triangular",
    "perp-basis", "spot-spread", "cex-dex", "options-vol",
    "grid", "market-making", "trend",
    "stablecoin-yield", "pairs-trading",
}


def _build_status(app_state) -> StrategyStatusResponse:
    paper_session = getattr(app_state, "paper_session", None)
    paper_task = getattr(app_state, "paper_task", None)
    runner = getattr(app_state, "runner", None)

    paper_running = paper_session is not None and paper_task is not None and not paper_task.done()
    runner_running = runner is not None

    last_scan_at = getattr(runner, "_last_scan_at", None) if runner else None
    cfg = getattr(app_state, "strategy_cfg", {})

    pos_count = 0
    if paper_session is not None:
        executor = getattr(paper_session, "_executor", None)
        if executor:
            guard = getattr(executor, "_guard", None)
            if guard:
                pos_count = len(getattr(guard, "_positions", {}))

    settings = get_settings()
    return StrategyStatusResponse(
        paper_running=paper_running,
        runner_running=runner_running,
        last_scan_at=last_scan_at,
        open_positions=pos_count,
        current_config=StrategyConfig(
            min_apr_pct=str(cfg.get("entry", {}).get("min_apr_pct", "10.0")),
            max_position_notional_usd=str(cfg.get("position", {}).get("size_usd", "50")),
            max_concurrent_positions=cfg.get("position", {}).get("max_positions", 3),
            scan_interval_seconds=cfg.get("scan_interval_seconds", 60.0),
        ),
        trading_mode=settings.trading_mode.lower(),
    )


@router.get("/status", response_model=StrategyStatusResponse)
async def get_status(_: CurrentUser, request: Request) -> StrategyStatusResponse:
    return _build_status(request.app.state)


@router.post("/funding-rate/start", response_model=StrategyActionResponse)
async def start(_: CurrentUser, request: Request) -> StrategyActionResponse:
    await start_paper(request.app.state)
    return StrategyActionResponse(paper_running=True, timestamp=datetime.now(timezone.utc))


@router.post("/funding-rate/stop", response_model=StrategyActionResponse)
async def stop(_: CurrentUser, request: Request) -> StrategyActionResponse:
    await stop_paper(request.app.state)
    return StrategyActionResponse(paper_running=False, timestamp=datetime.now(timezone.utc))


@router.patch("/funding-rate/config", response_model=StrategyConfig)
async def update_config(
    _: CurrentUser, request: Request, body: ConfigPatchRequest
) -> StrategyConfig:
    patch = body.model_dump(exclude_none=True)
    cfg = patch_strategy_config(request.app.state, patch)
    return StrategyConfig(
        min_apr_pct=str(cfg.get("entry", {}).get("min_apr_pct", "10.0")),
        max_position_notional_usd=str(cfg.get("position", {}).get("size_usd", "50")),
        max_concurrent_positions=cfg.get("position", {}).get("max_positions", 3),
        scan_interval_seconds=cfg.get("scan_interval_seconds", 60.0),
    )


# ---------------------------------------------------------------------------
# spot-perp basis: B.1 monitor only — 暴露 scanner 状态 + 最新机会
# ---------------------------------------------------------------------------


@router.get("/spot-perp/opportunities", response_model=SpotPerpOpportunitiesResponse)
async def spot_perp_opportunities(
    _: CurrentUser, request: Request
) -> SpotPerpOpportunitiesResponse:
    """spot-perp 基差扫描器最新机会(每 60s 刷新)。"""
    runner = getattr(request.app.state, "spot_perp_runner", None)
    if runner is None:
        return SpotPerpOpportunitiesResponse(running=False, last_scan_at=None, data=[])
    opps = [
        SpotPerpOpportunityOut(**opp.to_dict()) for opp in runner.latest_opportunities
    ]
    return SpotPerpOpportunitiesResponse(
        running=runner.is_running,
        last_scan_at=runner.last_scan_at,
        data=opps,
    )


# ---------------------------------------------------------------------------
# 多策略通用 start/stop (Phase 1+ 真实接入, 当前仅 funding-rate 已实现)
# ---------------------------------------------------------------------------


@router.post("/{strategy_id}/start", response_model=StrategyActionResponse)
async def start_any(
    _: CurrentUser, request: Request, strategy_id: str
) -> StrategyActionResponse:
    """启动指定策略。funding-rate 真实启动,其他策略返回 mock(等待 Phase 1+ 实现)。"""
    if strategy_id not in _VALID_STRATEGY_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown strategy: {strategy_id}")
    if strategy_id in _LIVE_STRATEGIES:
        await start_paper(request.app.state)
        return StrategyActionResponse(paper_running=True, timestamp=datetime.now(timezone.utc))
    # 未实现策略:返回响应壳子,前端展示 "queued"
    return StrategyActionResponse(paper_running=False, timestamp=datetime.now(timezone.utc))


@router.post("/{strategy_id}/stop", response_model=StrategyActionResponse)
async def stop_any(
    _: CurrentUser, request: Request, strategy_id: str
) -> StrategyActionResponse:
    """停止指定策略。"""
    if strategy_id not in _VALID_STRATEGY_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown strategy: {strategy_id}")
    if strategy_id in _LIVE_STRATEGIES:
        await stop_paper(request.app.state)
        return StrategyActionResponse(paper_running=False, timestamp=datetime.now(timezone.utc))
    return StrategyActionResponse(paper_running=False, timestamp=datetime.now(timezone.utc))
