"""Strategies 路由。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.api.v1.schemas.strategies import (
    ConfigPatchRequest,
    FundingRateOpportunitiesResponse,
    FundingRateOpportunityOut,
    SpotPerpConfigPatchRequest,
    SpotPerpConfigResponse,
    SpotPerpOpportunitiesResponse,
    SpotPerpOpportunityOut,
    StrategyActionResponse,
    StrategyConfig,
    StrategyStatusResponse,
)
from app.services.runtime_overrides import save_spot_perp_overrides
from app.services.strategy_control import (
    is_spot_perp_running,
    patch_strategy_config,
    start_paper,
    start_spot_perp,
    stop_paper,
    stop_spot_perp,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])

# 已实现真实控制的策略
_LIVE_STRATEGIES = {"funding-rate", "spot-perp"}

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
# funding-rate: 实时机会（候选展示，含 passes_entry 标志）
# ---------------------------------------------------------------------------


@router.get(
    "/funding-rate/opportunities",
    response_model=FundingRateOpportunitiesResponse,
)
async def funding_rate_opportunities(
    _: CurrentUser, request: Request
) -> FundingRateOpportunitiesResponse:
    """funding-rate scanner 最新机会（每 60s 刷新）。

    返回所有 APR ≥ scan_threshold_apr_pct 的候选，包含 ``passes_entry`` 标志：
      True  → APR ≥ min_apr_pct，paper_trading 会真实开仓
      False → 仅展示，未到入场门槛
    """
    from decimal import Decimal  # noqa: PLC0415
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        return FundingRateOpportunitiesResponse(
            running=False, last_scan_at=None,
            min_apr_pct="0", scan_threshold_apr_pct="0", data=[],
        )
    cfg = runner._scanner._config  # ScannerConfig
    min_apr = cfg.min_apr_pct
    out: list[FundingRateOpportunityOut] = []
    for opp in runner.latest_opportunities:
        apr = opp.apr_pct
        distance = max(Decimal("0"), min_apr - apr)
        out.append(FundingRateOpportunityOut(
            symbol=str(opp.symbol),
            exchange=opp.exchange,
            apr_pct=str(apr.quantize(Decimal("0.01"))),
            funding_rate=str(opp.funding_rate.rate),
            funding_interval_hours=float(opp.funding_rate.funding_interval_hours),
            next_funding_time_ms=int(opp.funding_rate.next_funding_time or 0),
            history_positive_count=opp.history_positive_count,
            history_total_count=opp.history_total_count,
            spot_depth_usd=str(opp.spot_depth_usd.quantize(Decimal("1"))),
            perp_depth_usd=str(opp.perp_depth_usd.quantize(Decimal("1"))),
            passes_entry=apr >= min_apr,  # request-time 现算，PATCH min_apr 即时反映
            distance_to_entry_pct=str(distance.quantize(Decimal("0.01"))),
        ))
    return FundingRateOpportunitiesResponse(
        running=runner.is_running,
        last_scan_at=runner.last_scan_at,
        min_apr_pct=str(min_apr),
        scan_threshold_apr_pct=str(cfg.effective_scan_threshold),
        data=out,
    )


# ---------------------------------------------------------------------------
# spot-perp basis: B.1 monitor only — 暴露 scanner 状态 + 最新机会
# ---------------------------------------------------------------------------


@router.get("/spot-perp/opportunities", response_model=SpotPerpOpportunitiesResponse)
async def spot_perp_opportunities(
    _: CurrentUser, request: Request
) -> SpotPerpOpportunitiesResponse:
    """spot-perp 基差扫描器最新机会(每 60s 刷新)。

    返回当前生效的入场门槛（entry_pct + per-direction），让 UI 渲染"距入场"列。
    """
    runner = getattr(request.app.state, "spot_perp_runner", None)
    if runner is None:
        return SpotPerpOpportunitiesResponse(running=False, last_scan_at=None, data=[])
    opps = [
        SpotPerpOpportunityOut(**opp.to_dict()) for opp in runner.latest_opportunities
    ]
    # 从 paper_session.cfg 读当前生效阈值（含 runtime override）；session 不存在时回退 0
    sp_paper = getattr(request.app.state, "spot_perp_paper", None)
    cfg = getattr(sp_paper, "cfg", None) if sp_paper else None
    return SpotPerpOpportunitiesResponse(
        running=runner.is_running,
        last_scan_at=runner.last_scan_at,
        entry_pct=str(cfg.entry_pct) if cfg else "0",
        entry_pct_premium=str(cfg.entry_pct_premium) if cfg else "0",
        entry_pct_discount=str(cfg.entry_pct_discount) if cfg else "0",
        scan_threshold_pct=str(cfg.scan_threshold_pct) if cfg else "0",
        data=opps,
    )


# ---------------------------------------------------------------------------
# spot-perp 配置 GET / PATCH (D.1.5 — UI 调阈值)
# ---------------------------------------------------------------------------


def _build_default_spot_perp_cfg():
    """无 session 时（已 stop）回退到 yaml + overrides 构造配置展示。"""
    import yaml as _yaml  # noqa: PLC0415
    from app.core.config import get_settings  # noqa: PLC0415
    from app.services.runtime_overrides import load_overrides  # noqa: PLC0415
    from app.strategies.spot_perp_basis.paper_trading import (  # noqa: PLC0415
        SpotPerpStrategyConfig,
    )

    try:
        with open("config/strategies/spot_perp_main.yaml") as f:
            yaml_data = _yaml.safe_load(f) or {}
    except FileNotFoundError:
        yaml_data = {}
    cfg = SpotPerpStrategyConfig.from_yaml(yaml_data)
    sp_overrides = (load_overrides() or {}).get("spot_perp") or {}
    if sp_overrides:
        cfg = cfg.apply_overrides(sp_overrides)
    settings = get_settings()
    if settings.spot_perp_notional_usd:
        cfg = cfg.apply_overrides(
            {"notional_per_position": settings.spot_perp_notional_usd}
        )
    return cfg


def _spot_perp_cfg_response(session, app_state) -> SpotPerpConfigResponse:
    """构造响应。session=None 时退回 yaml/overrides，session_running=False。"""
    if session is None:
        cfg = _build_default_spot_perp_cfg()
        from app.core.config import get_settings  # noqa: PLC0415
        live_mode = get_settings().trading_mode.lower() == "live"
    else:
        cfg = session.cfg
        live_mode = session.live_mode
    return SpotPerpConfigResponse(
        enabled=cfg.enabled,
        entry_pct=str(cfg.entry_pct),
        entry_pct_premium=str(cfg.entry_pct_premium),
        entry_pct_discount=str(cfg.entry_pct_discount),
        exit_pct=str(cfg.exit_pct),
        max_hold_hours=str(cfg.max_hold_hours),
        min_hold_minutes=str(cfg.min_hold_minutes),
        stop_basis_widening_pct=str(cfg.stop_basis_widening_pct),
        max_concurrent=cfg.max_concurrent,
        notional_per_position=str(cfg.notional_per_position),
        direction_filter=cfg.direction_filter,
        scan_threshold_pct=str(cfg.scan_threshold_pct),
        candidate_symbols=list(cfg.candidate_symbols),
        exchanges=list(cfg.exchanges),
        live_mode=live_mode,
        session_running=is_spot_perp_running(app_state),
    )


@router.get("/spot-perp/config", response_model=SpotPerpConfigResponse)
async def get_spot_perp_config(
    _: CurrentUser, request: Request
) -> SpotPerpConfigResponse:
    """读 spot-perp 当前生效配置（含 UI override 后）。stopped 时退回 yaml。"""
    session = getattr(request.app.state, "spot_perp_paper", None)
    return _spot_perp_cfg_response(session, request.app.state)


@router.patch("/spot-perp/config", response_model=SpotPerpConfigResponse)
async def patch_spot_perp_config(
    body: SpotPerpConfigPatchRequest,
    _: CurrentUser,
    request: Request,
) -> SpotPerpConfigResponse:
    """热更新 spot-perp 配置（下一 tick 生效）+ 持久化到 overrides.json。"""
    session = getattr(request.app.state, "spot_perp_paper", None)
    patch = body.model_dump(exclude_none=True)
    if patch:
        save_spot_perp_overrides(patch)
        if session is not None:
            session.update_cfg(patch)
    return _spot_perp_cfg_response(session, request.app.state)


# ---------------------------------------------------------------------------
# 多策略通用 start/stop (Phase 1+ 真实接入, 当前仅 funding-rate 已实现)
# ---------------------------------------------------------------------------


@router.post("/{strategy_id}/start", response_model=StrategyActionResponse)
async def start_any(
    _: CurrentUser, request: Request, strategy_id: str
) -> StrategyActionResponse:
    """启动指定策略。funding-rate / spot-perp 真实启动；其他策略返回 mock。"""
    if strategy_id not in _VALID_STRATEGY_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown strategy: {strategy_id}")
    state = request.app.state
    if strategy_id == "funding-rate":
        await start_paper(state)
        return StrategyActionResponse(paper_running=True, timestamp=datetime.now(timezone.utc))
    if strategy_id == "spot-perp":
        await start_spot_perp(state)
        return StrategyActionResponse(
            paper_running=is_spot_perp_running(state),
            timestamp=datetime.now(timezone.utc),
        )
    # 未实现策略:返回响应壳子,前端展示 "queued"
    return StrategyActionResponse(paper_running=False, timestamp=datetime.now(timezone.utc))


@router.post("/{strategy_id}/stop", response_model=StrategyActionResponse)
async def stop_any(
    _: CurrentUser, request: Request, strategy_id: str
) -> StrategyActionResponse:
    """停止指定策略（持仓不自动平仓）。"""
    if strategy_id not in _VALID_STRATEGY_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown strategy: {strategy_id}")
    state = request.app.state
    if strategy_id == "funding-rate":
        await stop_paper(state)
        return StrategyActionResponse(paper_running=False, timestamp=datetime.now(timezone.utc))
    if strategy_id == "spot-perp":
        await stop_spot_perp(state)
        return StrategyActionResponse(
            paper_running=is_spot_perp_running(state),
            timestamp=datetime.now(timezone.utc),
        )
    return StrategyActionResponse(paper_running=False, timestamp=datetime.now(timezone.utc))
