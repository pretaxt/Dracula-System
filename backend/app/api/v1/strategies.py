"""Strategies 路由。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser
from app.api.v1.schemas.strategies import (
    ConfigPatchRequest,
    StrategyActionResponse,
    StrategyConfig,
    StrategyStatusResponse,
)
from app.services.strategy_control import patch_strategy_config, start_paper, stop_paper

router = APIRouter(prefix="/strategies", tags=["strategies"])


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

    return StrategyStatusResponse(
        paper_running=paper_running,
        runner_running=runner_running,
        last_scan_at=last_scan_at,
        open_positions=pos_count,
        current_config=StrategyConfig(
            min_apr_pct=str(cfg.get("entry", {}).get("min_apr_pct", "10.0")),
            max_position_notional_usd=str(cfg.get("position", {}).get("size_usd", "500")),
            max_concurrent_positions=cfg.get("position", {}).get("max_positions", 3),
            scan_interval_seconds=cfg.get("scan_interval_seconds", 60.0),
        ),
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
        max_position_notional_usd=str(cfg.get("position", {}).get("size_usd", "500")),
        max_concurrent_positions=cfg.get("position", {}).get("max_positions", 3),
        scan_interval_seconds=cfg.get("scan_interval_seconds", 60.0),
    )
