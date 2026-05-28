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
    PerpBasisConfigPatchRequest,
    PerpBasisConfigResponse,
    PerpBasisOpportunitiesResponse,
    PerpBasisOpportunityOut,
    PriceSpreadOpportunitiesResponse,
    PriceSpreadOpportunityOut,
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
    is_cex_dex_running,
    is_spot_perp_running,
    patch_strategy_config,
    start_cex_dex,
    start_paper,
    is_perp_basis_paper_running,
    start_perp_basis_paper,
    start_spot_perp,
    stop_cex_dex,
    stop_perp_basis_paper,
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
    """#01 funding-rate 配置 PATCH — 完整路径：
       1. 持久化到 overrides.json（重启可恢复）
       2. 同步 scanner._config（下一 tick 生效，不等重启）
       3. 同步 strategy_cfg（GET /status 返回最新值）
    """
    from app.services.runtime_overrides import save_overrides  # noqa: PLC0415
    from decimal import Decimal as _Dec  # noqa: PLC0415

    raw_patch = body.model_dump(exclude_none=True)
    if not raw_patch:
        # 空 patch 直接回当前
        cfg = getattr(request.app.state, "strategy_cfg", {}) or {}
    else:
        # 1. 持久化 overrides（仅白名单字段会写）
        overrides_patch: dict = {}
        if "min_apr_pct" in raw_patch:
            overrides_patch["min_apr_pct"] = raw_patch["min_apr_pct"]
        if "max_concurrent_positions" in raw_patch:
            overrides_patch["max_positions"] = raw_patch["max_concurrent_positions"]
        # max_position_notional_usd 暂未在 overrides 白名单中，需要时再加
        if overrides_patch:
            try:
                save_overrides(overrides_patch)
            except Exception:
                pass  # 持久化失败不阻断 in-memory 更新

        # 2. 同步 scanner._config（APR 阈值）
        new_apr = raw_patch.get("min_apr_pct")
        for owner_name in ("paper_session", "runner"):
            owner = getattr(request.app.state, owner_name, None)
            scanner = getattr(owner, "_scanner", None) if owner else None
            scfg = getattr(scanner, "_config", None) if scanner else None
            if scfg is not None and new_apr is not None and hasattr(scfg, "min_apr_pct"):
                try:
                    scfg.min_apr_pct = _Dec(str(new_apr))
                except Exception:
                    pass

        # 2b. 同步 live RiskLimits.max_positions — paper_session._executor._guard.limits
        # 否则 max_concurrent_positions PATCH 看似成功但 RiskGuard 仍用 boot 期旧值。
        new_max_pos = raw_patch.get("max_concurrent_positions")
        if new_max_pos is not None:
            try:
                sess = getattr(request.app.state, "paper_session", None)
                executor = getattr(sess, "_executor", None) if sess else None
                guard = getattr(executor, "_guard", None) if executor else None
                if guard is not None and hasattr(guard, "limits"):
                    guard.limits.max_positions = int(new_max_pos)
            except Exception:
                pass

        # 3. 同步 strategy_cfg（GET /status 读这里）
        cfg = patch_strategy_config(request.app.state, raw_patch)
        # patch_strategy_config 只做 flat update，把 min_apr_pct 写到 entry.* 嵌套
        if new_apr is not None:
            cfg.setdefault("entry", {})["min_apr_pct"] = str(new_apr)
        if "max_concurrent_positions" in raw_patch:
            cfg.setdefault("position", {})["max_positions"] = int(raw_patch["max_concurrent_positions"])
        if "max_position_notional_usd" in raw_patch:
            cfg.setdefault("position", {})["size_usd"] = str(raw_patch["max_position_notional_usd"])
        if "scan_interval_seconds" in raw_patch:
            cfg["scan_interval_seconds"] = float(raw_patch["scan_interval_seconds"])
        request.app.state.strategy_cfg = cfg

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
    # paper_session 持有真实的 pre_funding_window_minutes（默认 15）
    paper = getattr(request.app.state, "paper_session", None)
    pre_window = float(getattr(paper, "_pre_funding_window_min", 15.0) or 15.0)
    return FundingRateOpportunitiesResponse(
        running=runner.is_running,
        last_scan_at=runner.last_scan_at,
        min_apr_pct=str(min_apr),
        scan_threshold_apr_pct=str(cfg.effective_scan_threshold),
        pre_funding_window_minutes=pre_window,
        data=out,
    )


# ---------------------------------------------------------------------------
# #02 perp-basis: 跨所 funding 差套利 (Phase A monitor only)
# ---------------------------------------------------------------------------


@router.get("/perp-basis/exchange-balance")
async def perp_basis_exchange_balance(
    _: CurrentUser, request: Request,
) -> dict:
    """#02-4: per-exchange 总可动用 USDT — 累加所有可划转到 perp 钱包的子账户。

    设计：策略开仓前 ``_ensure_perp_margin`` 会自动跨钱包级联划转
    （binance: spot/cross-margin/funding → perp；htx: spot → swap；
     okx UTA 共享无需划转）。所以"可用"应该看 **所有 USDT 钱包累加**，
    而不是单独 perp 钱包。

    数据源：reconciler.balance_cache（30s 周期更新）。
    """
    # 与 perp_basis paper_trading._USABLE_USDT_KEYS 同义 — 单一真相
    _USABLE = {
        "binance": ("USDT_PERP", "USDT", "USDT_MARGIN", "USDT_FUNDING"),
        "htx": ("USDT_HTX_SWAP", "USDT"),
        "okx": ("USDT",),
        "bybit": ("USDT",),
        "bitget": ("USDT",),
    }
    rec = getattr(request.app.state, "balance_reconciler", None)
    if rec is None or not getattr(rec, "balance_cache", None):
        return {"data": [], "ready": False}
    out: list[dict] = []
    for ex_name, assets in (rec.balance_cache or {}).items():
        if not isinstance(assets, dict):
            continue
        keys = _USABLE.get(ex_name, ("USDT",))
        breakdown: dict[str, float] = {}
        total_free = 0.0
        total_total = 0.0
        for k in keys:
            info = assets.get(k)
            if not isinstance(info, dict):
                continue
            try:
                f = float(info.get("free") or 0)
                t = float(info.get("total") or 0)
            except Exception:
                continue
            if t > 0:
                breakdown[k] = round(f, 4)
                total_free += f
                total_total += t
        if total_total <= 0:
            continue
        out.append({
            "exchange": ex_name,
            "perp_usdt_free": str(round(total_free, 4)),
            "perp_usdt_total": str(round(total_total, 4)),
            "wallet_breakdown": breakdown,  # 显示各子钱包详情
            "ready": total_free >= 10.0,    # 至少 $10 才能开 $50 × 5 leverage
        })
    return {
        "data": sorted(out, key=lambda x: x["exchange"]),
        "ready": sum(1 for r in out if r["ready"]) >= 2,  # ≥ 2 个 exchange 就绪 = 跨所可用
    }


@router.get("/perp-basis/config", response_model=PerpBasisConfigResponse)
async def perp_basis_config(_: CurrentUser, request: Request) -> PerpBasisConfigResponse:
    """读取 #02 perp_basis 当前生效配置（yaml + override 合并）。"""
    import yaml as _yaml  # noqa: PLC0415
    from app.services.runtime_overrides import (  # noqa: PLC0415
        load_overrides as _load_overrides,
        apply_to_perp_basis_cfg as _apply_pb,
    )
    state = request.app.state
    runner = getattr(state, "perp_basis_runner", None)
    cfg: dict = {}
    try:
        with open("config/strategies/perp_basis_main.yaml") as f:
            cfg = _yaml.safe_load(f) or {}
    except Exception:
        cfg = {}
    pb_overrides = (_load_overrides() or {}).get("perp_basis") or {}
    if isinstance(pb_overrides, dict) and pb_overrides:
        cfg = _apply_pb(cfg, pb_overrides)
    entry = cfg.get("entry", {}) or {}
    pos_cfg = cfg.get("position", {}) or {}
    exit_cfg = cfg.get("exit", {}) or {}
    scan_cfg = cfg.get("scanning", {}) or {}
    risk_cfg = cfg.get("risk", {}) or {}
    scanner_cfg = runner._scanner._config if runner is not None else None
    paper = getattr(state, "perp_basis_paper", None)
    paper_task = getattr(state, "perp_basis_paper_task", None)
    paper_running = (
        paper is not None and paper_task is not None and not paper_task.done()
    )
    return PerpBasisConfigResponse(
        enabled=bool(cfg.get("enabled", True)),
        paper_running=paper_running,
        min_diff_apr_pct=str(
            scanner_cfg.min_diff_apr_pct if scanner_cfg
            else entry.get("min_diff_apr_pct", "50.0")
        ),
        max_concurrent=int(pos_cfg.get("max_concurrent", 2)),
        notional_per_position=str(pos_cfg.get("notional_per_position", "50")),
        max_hold_hours=str(exit_cfg.get("max_hold_hours", "48")),
        min_hold_hours=str(exit_cfg.get("min_hold_hours", "4")),
        exit_diff_apr_pct=str(exit_cfg.get("exit_diff_apr_pct", "5")),
        stop_price_divergence_pct=str(
            paper._stop_price_div if paper is not None
            else risk_cfg.get("stop_price_divergence_pct", "2.0")
        ),
        candidate_symbols=list(pos_cfg.get("candidate_symbols", []) or []),
        scan_interval_seconds=float(scan_cfg.get("scan_interval_seconds", 30)),
    )


@router.patch("/perp-basis/config", response_model=PerpBasisConfigResponse)
async def perp_basis_config_patch(
    _: CurrentUser, request: Request, body: PerpBasisConfigPatchRequest,
) -> PerpBasisConfigResponse:
    """热更新 #02 perp_basis 参数（min_diff / max_concurrent 等）。

    立即作用于 scanner._config + 已运行的 paper session（下个 tick 生效）。
    """
    from decimal import Decimal as _Decimal  # noqa: PLC0415
    state = request.app.state
    runner = getattr(state, "perp_basis_runner", None)
    paper = getattr(state, "perp_basis_paper", None)
    patch = body.model_dump(exclude_none=True)

    # 1. scanner 配置（影响候选过滤）
    if runner is not None and "min_diff_apr_pct" in patch:
        runner._scanner._config.min_diff_apr_pct = _Decimal(str(patch["min_diff_apr_pct"]))

    # 2. paper session 配置（影响入场/出场决策）
    if paper is not None:
        if "min_diff_apr_pct" in patch:
            paper._min_diff = _Decimal(str(patch["min_diff_apr_pct"]))
        if "max_concurrent" in patch:
            paper._max_concurrent = int(patch["max_concurrent"])
        if "notional_per_position" in patch:
            paper._notional = _Decimal(str(patch["notional_per_position"]))
        if "max_hold_hours" in patch:
            paper._max_hold = _Decimal(str(patch["max_hold_hours"]))
        if "min_hold_hours" in patch:
            paper._min_hold = _Decimal(str(patch["min_hold_hours"]))
        if "exit_diff_apr_pct" in patch:
            paper._exit_diff = _Decimal(str(patch["exit_diff_apr_pct"]))
        if "stop_price_divergence_pct" in patch:
            paper._stop_price_div = _Decimal(str(patch["stop_price_divergence_pct"]))
        if "max_entry_price_divergence_pct" in patch:
            paper._max_entry_price_div = _Decimal(str(patch["max_entry_price_divergence_pct"]))

    # 3. 持久化到 overrides.json（重启不丢）
    try:
        from app.services.runtime_overrides import save_perp_basis_overrides  # noqa: PLC0415
        save_perp_basis_overrides(patch)
    except Exception:  # noqa: BLE001
        # 持久化失败不影响内存生效
        pass

    return await perp_basis_config(_, request)


@router.get(
    "/perp-basis/opportunities",
    response_model=PerpBasisOpportunitiesResponse,
)
async def perp_basis_opportunities(
    _: CurrentUser, request: Request,
) -> PerpBasisOpportunitiesResponse:
    """跨所 perp funding diff 实时机会（每 30s 刷新）。

    数据源：MarketDataHub funding_rates 缓存（不重复打 exchange）。
    """
    runner = getattr(request.app.state, "perp_basis_runner", None)
    if runner is None:
        return PerpBasisOpportunitiesResponse(
            running=False, last_scan_at=None,
            min_diff_apr_pct="0", exchange_pair_count=0, data=[],
        )
    cfg = runner._scanner._config
    return PerpBasisOpportunitiesResponse(
        running=runner.is_running,
        last_scan_at=runner.last_scan_at,
        min_diff_apr_pct=str(cfg.min_diff_apr_pct),
        exchange_pair_count=len(cfg.exchange_pairs),
        data=[
            PerpBasisOpportunityOut(**opp.to_dict())
            for opp in runner.latest_opportunities
        ],
    )


# ---------------------------------------------------------------------------
# #03 price-spread: Phase A monitor — 跨所价格差实时机会
# ---------------------------------------------------------------------------


@router.get(
    "/price-spread/opportunities",
    response_model=PriceSpreadOpportunitiesResponse,
)
async def price_spread_opportunities(
    _: CurrentUser, request: Request,
) -> PriceSpreadOpportunitiesResponse:
    """跨所 perp 价格差实时机会（每 30s 刷新）。

    数据源：MarketDataHub tickers 缓存（同步无 IO，和 #02 一样快）。
    spread_pct = (short_price - long_price) / long_price × 100
    """
    runner = getattr(request.app.state, "price_spread_runner", None)
    if runner is None:
        return PriceSpreadOpportunitiesResponse(
            running=False, last_scan_at=None,
            min_spread_pct="0", exchange_pair_count=0, data=[],
        )
    cfg = runner._scanner._config
    return PriceSpreadOpportunitiesResponse(
        running=runner.is_running,
        last_scan_at=runner.last_scan_at,
        min_spread_pct=str(cfg.min_spread_pct),
        exchange_pair_count=len(cfg.exchange_pairs),
        data=[
            PriceSpreadOpportunityOut(**opp.to_dict())
            for opp in runner.latest_opportunities
        ],
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
        peak_window_minutes=str(cfg.peak_window_minutes),
        min_peak_dropoff_pct=str(cfg.min_peak_dropoff_pct),
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
    if strategy_id == "perp-basis":
        await start_perp_basis_paper(state)
        return StrategyActionResponse(
            paper_running=is_perp_basis_paper_running(state),
            timestamp=datetime.now(timezone.utc),
        )
    if strategy_id == "cex-dex":
        await start_cex_dex(state)
        return StrategyActionResponse(
            paper_running=is_cex_dex_running(state),
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
    if strategy_id == "perp-basis":
        await stop_perp_basis_paper(state)
        return StrategyActionResponse(
            paper_running=is_perp_basis_paper_running(state),
            timestamp=datetime.now(timezone.utc),
        )
    if strategy_id == "cex-dex":
        await stop_cex_dex(state)
        return StrategyActionResponse(
            paper_running=is_cex_dex_running(state),
            timestamp=datetime.now(timezone.utc),
        )
    return StrategyActionResponse(paper_running=False, timestamp=datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# 聚合所有策略机会 — dashboard "实时套利机会" 卡片用
# ---------------------------------------------------------------------------

@router.get("/all-opportunities")
async def all_opportunities(_: CurrentUser, request: Request) -> dict:
    """汇总当前所有策略的实时机会，统一 schema 返回。

    schema: [{strategy, symbol, exchange, apr_pct, extra_pct, meta}, ...]
      - apr_pct: 主指标（funding_rate APR / diff_apr_pct / spread_pct）
      - extra_pct: 次指标（funding_rate 本身 / 价差 / 基差）
      - exchange: 单交易所 or "long_ex→short_ex"

    数据源：4 个 runner 内存 latest_opportunities（不打交易所，零延迟）。
    """
    out: list[dict] = []
    state = request.app.state

    # #01 funding_rate
    fr_runner = getattr(state, "funding_rate_runner", None)
    if fr_runner is not None:
        for o in getattr(fr_runner, "latest_opportunities", []) or []:
            try:
                out.append({
                    "strategy": "funding_rate",
                    "symbol": str(o.symbol),
                    "exchange": str(o.exchange),
                    "apr_pct": str(o.apr_pct),
                    "extra_pct": str(o.funding_rate.rate * 100) if getattr(o, "funding_rate", None) else "0",
                    "meta": "",
                })
            except Exception:
                continue

    # #02 perp_basis
    pb_runner = getattr(state, "perp_basis_runner", None)
    if pb_runner is not None:
        for o in getattr(pb_runner, "latest_opportunities", []) or []:
            try:
                d = o.to_dict()
                out.append({
                    "strategy": "perp_basis",
                    "symbol": d["symbol"],
                    "exchange": f"{d['long_exchange']}→{d['short_exchange']}",
                    "apr_pct": d["diff_apr_pct"],
                    "extra_pct": str(round(float(d["short_apr_pct"]) - float(d["long_apr_pct"]), 2)),
                    "meta": d.get("health_tier", ""),
                })
            except Exception:
                continue

    # #03 price_spread
    ps_runner = getattr(state, "price_spread_runner", None)
    if ps_runner is not None:
        for o in getattr(ps_runner, "latest_opportunities", []) or []:
            try:
                d = o.to_dict() if hasattr(o, "to_dict") else dict(vars(o))
                ex_label = (
                    f"{d.get('long_exchange', '?')}→{d.get('short_exchange', '?')}"
                    if d.get("long_exchange")
                    else str(d.get("exchange", "?"))
                )
                # apr_pct 用年化估算（spread × 365 × 周转次数）；这里直接用 spread_pct 作为机会强度
                out.append({
                    "strategy": "price_spread",
                    "symbol": d.get("symbol", "?"),
                    "exchange": ex_label,
                    "apr_pct": str(d.get("spread_pct", "0")),
                    "extra_pct": str(d.get("spread_pct", "0")),
                    "meta": "",
                })
            except Exception:
                continue

    # #04 spot_perp
    sp_runner = getattr(state, "spot_perp_runner", None)
    if sp_runner is not None:
        for o in getattr(sp_runner, "latest_opportunities", []) or []:
            try:
                d = o.to_dict() if hasattr(o, "to_dict") else dict(vars(o))
                out.append({
                    "strategy": "spot_perp",
                    "symbol": d.get("symbol", "?"),
                    "exchange": str(d.get("exchange", "?")),
                    "apr_pct": str(d.get("apr_pct", d.get("basis_pct", "0"))),
                    "extra_pct": str(d.get("basis_pct", "0")),
                    "meta": str(d.get("direction", "")),
                })
            except Exception:
                continue

    # 按 apr_pct 数值降序
    def _key(item: dict) -> float:
        try:
            return float(item.get("apr_pct", "0") or 0)
        except (ValueError, TypeError):
            return 0.0
    out.sort(key=_key, reverse=True)
    return {"data": out, "count": len(out)}


# ----------------------------------------------------------------------
# dgr_btc LIVE runtime metrics (§6.2 #4)
# ----------------------------------------------------------------------


@router.get("/dgr-btc/health")
async def get_dgr_btc_health(_: CurrentUser) -> dict:
    """dgr_btc LIVE 模式 3 个关键运行指标:
      - maker_fill_rate (post-only 拒单率)
      - latency p95 (broker 健康度)
      - safety_reject_per_hour (配置 bug 信号)

    每个指标含 value / threshold / status (ok/warning/breach/insufficient_data).
    无活跃 collector 时返回 active=false (策略未启动或非 LIVE 模式).
    """
    from app.strategies.dgr_btc.live_metrics import list_collectors  # noqa: PLC0415

    collectors = list_collectors()
    if not collectors:
        return {
            "active": False,
            "message": "no dgr_btc LIVE collector active (strategy not started or not in LIVE mode)",
            "instances": [],
        }

    instances = [c.health() for c in collectors.values()]
    # 聚合 overall status: 任一 breach → breach; 任一 warning → warning; 否则 ok
    overall = "ok"
    for inst in instances:
        for m in inst.get("metrics", {}).values():
            st = m.get("status")
            if st == "breach":
                overall = "breach"
                break
            if st == "warning" and overall == "ok":
                overall = "warning"
        if overall == "breach":
            break

    return {
        "active": True,
        "overall_status": overall,
        "instances": instances,
        "n_instances": len(instances),
    }
