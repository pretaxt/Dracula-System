"""Risk 路由 — GET /risk/limits, PATCH /risk/limits, GET /risk/events。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.risk import RiskEventOut, RiskEventsResponse, RiskLimitsOut, RiskLimitsPatch
from app.risk.limits import RiskLimits
from app.services.dashboard_service import get_summary
from app.services.system_service import get_recent_risk_events

router = APIRouter(prefix="/risk", tags=["risk"])


def _limits_to_out(limits: RiskLimits) -> RiskLimitsOut:
    return RiskLimitsOut(
        max_positions=limits.max_positions,
        stop_loss_pct=str(limits.stop_loss_pct),
        max_hold_hours=str(limits.max_hold_hours),
        min_apr_pct=str(limits.min_apr_pct),
        max_total_notional_usd=str(limits.max_total_notional_usd),
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

    return _limits_to_out(limits)


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
