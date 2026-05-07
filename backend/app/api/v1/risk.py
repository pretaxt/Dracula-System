"""Risk 路由 — GET /risk/limits, PATCH /risk/limits, GET /risk/events。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import CurrentUser
from app.api.v1.schemas.risk import RiskEventOut, RiskEventsResponse, RiskLimitsOut, RiskLimitsPatch
from app.risk.limits import RiskLimits

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
# Events — TODO Week 6+ 替换为真实 risk_events 表查询
# ---------------------------------------------------------------------------

_MOCK_EVENTS_TEMPLATE = [
    {"hours_ago":   8, "tier": "TIER 2", "event": "API 错误率告警", "trigger": "HTX 5m err rate",   "value": "3.2% / 5%", "action": "auto_recovered"},
    {"hours_ago":  16, "tier": "TIER 1", "event": "WebSocket 断连",  "trigger": "Bybit WS",          "value": "42s",       "action": "auto_recovered"},
    {"hours_ago":  72, "tier": "TIER 2", "event": "资金费率反转",   "trigger": "ARB/USDT funding",  "value": "-0.018%",   "action": "closed"},
    {"hours_ago": 192, "tier": "TIER 1", "event": "建仓滑点超阈值", "trigger": "Binance ETH/USDT", "value": "0.34%",     "action": "cancelled"},
]


@router.get("/events", response_model=RiskEventsResponse)
async def list_events(
    _: CurrentUser,
    days: int = Query(default=30, ge=1, le=365),
) -> RiskEventsResponse:
    """风控事件日志。当前为 mock 数据,Week 6+ 替换为真实表查询。"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    events: list[RiskEventOut] = []
    for tpl in _MOCK_EVENTS_TEMPLATE:
        event_time = now - timedelta(hours=int(tpl["hours_ago"]))
        if event_time < cutoff:
            continue
        action = str(tpl["action"])
        events.append(
            RiskEventOut(
                time=event_time,
                tier=str(tpl["tier"]),
                event=str(tpl["event"]),
                trigger=str(tpl["trigger"]),
                value=str(tpl["value"]),
                action=action,
                auto_recovered=action == "auto_recovered",
            )
        )

    return RiskEventsResponse(data=events, total=len(events), days=days)
