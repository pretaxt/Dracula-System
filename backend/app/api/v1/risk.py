"""Risk 路由 — GET /risk/limits, PATCH /risk/limits, GET /risk/events。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.risk import RiskEventOut, RiskEventsResponse, RiskLimitsOut, RiskLimitsPatch
from app.risk.limits import RiskLimits
from app.services.dashboard_service import get_summary
from app.services.runtime_overrides import save_overrides
from app.services.system_service import get_recent_risk_events

router = APIRouter(prefix="/risk", tags=["risk"])


def _limits_to_out(limits: RiskLimits) -> RiskLimitsOut:
    return RiskLimitsOut(
        max_positions=limits.max_positions,
        stop_loss_pct=str(limits.stop_loss_pct),
        max_hold_hours=str(limits.max_hold_hours),
        min_apr_pct=str(limits.min_apr_pct),
        max_total_notional_usd=str(limits.max_total_notional_usd),
        scan_threshold_apr_pct=str(getattr(limits, "scan_threshold_apr_pct", Decimal("0"))),
    )


def _get_limits(app_state) -> RiskLimits:
    session = getattr(app_state, "paper_session", None)
    if session:
        executor = getattr(session, "_executor", None)
        if executor:
            guard = getattr(executor, "_guard", None)
            if guard:
                return guard.limits
    return RiskLimits()


@router.get("/limits", response_model=RiskLimitsOut)
async def get_limits(_: CurrentUser, request: Request) -> RiskLimitsOut:
    return _limits_to_out(_get_limits(request.app.state))


@router.patch("/limits", response_model=RiskLimitsOut)
async def patch_limits(
    _: CurrentUser, request: Request, body: RiskLimitsPatch
) -> RiskLimitsOut:
    limits = _get_limits(request.app.state)
    patch = body.model_dump(exclude_none=True, exclude={"confirm_widening"})

    # 检测是否放宽限制
    widening = False
    if "max_positions" in patch and patch["max_positions"] > limits.max_positions:
        widening = True
    if "stop_loss_pct" in patch and Decimal(patch["stop_loss_pct"]) > limits.stop_loss_pct:
        widening = True
    if "max_total_notional_usd" in patch and Decimal(patch["max_total_notional_usd"]) > limits.max_total_notional_usd:
        widening = True

    if widening and not body.confirm_widening:
        raise HTTPException(
            status_code=400,
            detail="Widening risk limits requires confirm_widening=true",
        )

    for field, value in patch.items():
        setattr(limits, field, Decimal(str(value)) if isinstance(value, str) else value)

    # 同步到 scanner config 和 strategy_cfg —— 否则 scanner 用 YAML 老值预过滤，
    # UI 改 min_apr_pct / max_positions / max_total_notional_usd 显示成功但实际无效。
    _propagate_limits_to_runtime(request.app.state, patch)

    # 持久化到 /app/state/overrides.json —— 重启容器后 lifespan 自动加载
    save_overrides(patch)

    return _limits_to_out(limits)


def _propagate_limits_to_runtime(app_state, patch: dict) -> None:
    """把 risk_limits PATCH 同步到所有读到它的运行时组件。

    - paper_session._scanner._config.min_apr_pct / scan_threshold_apr_pct
    - runner._scanner._config.min_apr_pct / scan_threshold_apr_pct
    - app_state.strategy_cfg（让 /strategies/status 显示一致）
    """
    new_apr = patch.get("min_apr_pct")
    new_max_pos = patch.get("max_positions")
    new_max_notional = patch.get("max_total_notional_usd")
    new_scan_thresh = patch.get("scan_threshold_apr_pct")

    # 更新 scanner config —— scanner 在 scan() 里读 self._config.{min_apr_pct,scan_threshold_apr_pct}
    if new_apr is not None or new_scan_thresh is not None:
        for owner_name in ("paper_session", "runner"):
            owner = getattr(app_state, owner_name, None)
            if owner is None:
                continue
            scanner = getattr(owner, "_scanner", None)
            if scanner is None:
                continue
            scfg = getattr(scanner, "_config", None)
            if scfg is None:
                continue
            if new_apr is not None and hasattr(scfg, "min_apr_pct"):
                try:
                    scfg.min_apr_pct = Decimal(str(new_apr))
                except Exception:
                    pass
            if new_scan_thresh is not None and hasattr(scfg, "scan_threshold_apr_pct"):
                try:
                    scfg.scan_threshold_apr_pct = Decimal(str(new_scan_thresh))
                except Exception:
                    pass

    # 更新 strategy_cfg —— /strategies/status 从这里读 current_config 显示给前端
    cfg = getattr(app_state, "strategy_cfg", None)
    if cfg is not None:
        if new_apr is not None:
            cfg.setdefault("entry", {})["min_apr_pct"] = str(new_apr)
        if new_max_pos is not None:
            cfg.setdefault("position", {})["max_positions"] = int(new_max_pos)
        if new_max_notional is not None:
            cfg.setdefault("risk", {})["max_total_notional_usd"] = str(new_max_notional)
        if new_scan_thresh is not None:
            cfg.setdefault("entry", {})["scan_threshold_apr_pct"] = str(new_scan_thresh)


# ---------------------------------------------------------------------------
# Events — PositionRecord 历史 + 即时 daily DD 衍生
# ---------------------------------------------------------------------------


@router.get("/events", response_model=RiskEventsResponse)
async def list_events(
    _: CurrentUser,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
) -> RiskEventsResponse:
    """风控事件 — 来源:PositionRecord exit_reason 历史 + 当前 daily DD 阈值。"""
    summary = await get_summary(db)
    daily_dd = Decimal(summary.get("daily_drawdown_pct") or "0")

    raw = await get_recent_risk_events(db, days=days, daily_dd_pct=daily_dd)
    events = [RiskEventOut(**e) for e in raw]
    return RiskEventsResponse(data=events, total=len(events), days=days)


# ---------------------------------------------------------------------------
# Tier 3 账户级硬性熔断阈值（env var 驱动）— 暴露给前端 UI 动态展示
# ---------------------------------------------------------------------------

@router.get("/thresholds")
async def get_thresholds() -> dict:
    """返回 6 个账户级 circuit_breaker 阈值（从 env var 加载）。

    前端用于显示 Tier 3 "锁定红线" 卡片的红线值。
    修改方式：编辑 /opt/dracula/.env + docker compose restart api 即生效。
    """
    from app.services.risk_circuit_breaker import (  # noqa: PLC0415
        DAILY_DD_HALT_PCT,
        WEEKLY_DD_HALT_PCT,
        MIN_MARGIN_USAGE_PCT,
        MAX_EXCHANGE_CONCENTRATION_PCT,
        MAX_SYMBOL_CONCENTRATION_PCT,
        MAX_BINANCE_MMR_PCT,
    )
    return {
        "daily_dd_halt_pct": str(DAILY_DD_HALT_PCT),
        "weekly_dd_halt_pct": str(WEEKLY_DD_HALT_PCT),
        "min_margin_usage_pct": str(MIN_MARGIN_USAGE_PCT),
        "max_exchange_concentration_pct": str(MAX_EXCHANGE_CONCENTRATION_PCT),
        "max_symbol_concentration_pct": str(MAX_SYMBOL_CONCENTRATION_PCT),
        "max_binance_mmr_pct": str(MAX_BINANCE_MMR_PCT),
    }

