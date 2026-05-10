"""Positions 路由 — GET /positions, GET /positions/{uuid}, POST /positions/{uuid}/close。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.positions import (
    CloseRequest,
    CloseResponse,
    LegOut,
    PositionListMeta,
    PositionListResponse,
    PositionOut,
)
from app.core.logging import get_logger
from app.models.position import PositionLegRecord
from app.services.position_service import compute_days_held, get_position, list_positions

logger = get_logger(__name__)
router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("", response_model=PositionListResponse)
async def get_positions(
    _: CurrentUser,
    db: DbSession,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> PositionListResponse:
    records, total = await list_positions(db, status=status, page=page, page_size=page_size)
    data = [PositionOut.from_record(r, compute_days_held(r)) for r in records]
    return PositionListResponse(
        data=data,
        meta=PositionListMeta(page=page, page_size=page_size, total=total),
    )


@router.get("/{uuid}", response_model=PositionOut)
async def get_position_detail(
    _: CurrentUser, db: DbSession, request: Request, uuid: str,
) -> PositionOut:
    rec = await get_position(db, uuid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Position not found")
    out = PositionOut.from_record(rec, compute_days_held(rec))

    # 跨所策略 (#02 / #04) 加载 legs[] + 实时数据
    leg_rows = (await db.execute(
        select(PositionLegRecord).where(PositionLegRecord.position_id == rec.id)
    )).scalars().all()

    if not leg_rows:
        return out

    # 实时数据来自 MarketDataHub
    hub = getattr(request.app.state, "market_data_hub", None)

    legs_out: list[LegOut] = []
    apr_long: Decimal | None = None
    apr_short: Decimal | None = None
    price_long: Decimal | None = None
    price_short: Decimal | None = None

    for leg in leg_rows:
        cur_price: Decimal | None = None
        cur_rate: Decimal | None = None
        cur_apr: Decimal | None = None
        next_ms: int | None = None
        interval_h: int | None = None
        if hub is not None:
            try:
                from app.exchanges.models import InstrumentType, Symbol  # noqa: PLC0415
                inst = (
                    InstrumentType.PERPETUAL if leg.instrument_type == "perpetual"
                    else InstrumentType.SPOT
                )
                # leg.symbol stored as "BTC/USDT" or similar — try parse, fall back to raw
                try:
                    sym_obj = Symbol.from_ccxt(leg.symbol)
                except Exception:
                    sym_obj = leg.symbol  # type: ignore[assignment]
                # ticker
                t_entry = hub.get_ticker(leg.exchange, inst, sym_obj)
                if t_entry is not None:
                    px = getattr(t_entry, "last", None) or getattr(t_entry, "bid", None)
                    if px is not None:
                        try:
                            cur_price = Decimal(str(px))
                        except Exception:
                            pass
                # funding (仅 perpetual 有意义)
                if inst == InstrumentType.PERPETUAL:
                    fr_entry = hub.get_funding_rate(leg.exchange, sym_obj)
                    if fr_entry is not None:
                        fr = getattr(fr_entry, "rate", None)
                        if fr is not None:
                            cur_rate = Decimal(str(getattr(fr, "rate", 0)))
                            interval_h = int(getattr(fr, "funding_interval_hours", 8) or 8)
                            next_ms = int(getattr(fr, "next_funding_time", 0) or 0) or None
                            # APR = rate × periods/year × 100
                            periods = Decimal("24") / Decimal(interval_h) * Decimal("365")
                            cur_apr = cur_rate * periods * Decimal("100")
            except Exception:
                logger.debug("position_leg_live_fetch_failed", exchange=leg.exchange, symbol=leg.symbol)

        side_str = (leg.side or "").lower()
        legs_out.append(LegOut(
            exchange=leg.exchange,
            side=side_str,
            instrument_type=leg.instrument_type,
            symbol=leg.symbol,
            size=str(leg.size),
            entry_price=str(leg.entry_price or 0),
            current_price=str(cur_price) if cur_price is not None else None,
            current_funding_rate=str(cur_rate) if cur_rate is not None else None,
            current_apr_pct=str(cur_apr.quantize(Decimal("0.01"))) if cur_apr is not None else None,
            next_funding_time_ms=next_ms,
            funding_interval_hours=interval_h,
        ))

        # 收集 long/short 端用于 #02 派生指标
        if side_str in ("long", "buy"):
            apr_long = cur_apr
            price_long = cur_price
        elif side_str in ("short", "sell"):
            apr_short = cur_apr
            price_short = cur_price

    out.legs = legs_out

    # #02 perp_basis 派生指标
    if rec.strategy_instance == "perp_basis_main":
        if apr_long is not None and apr_short is not None:
            out.current_diff_apr_pct = str((apr_short - apr_long).quantize(Decimal("0.01")))
        if (
            price_long is not None and price_short is not None
            and price_long > 0 and price_short > 0
        ):
            mid = (price_long + price_short) / Decimal("2")
            div = abs(price_long - price_short) / mid * Decimal("100")
            out.current_price_divergence_pct = str(div.quantize(Decimal("0.0001")))

    return out


@router.post("/{uuid}/close", response_model=CloseResponse)
async def close_position(
    _: CurrentUser,
    db: DbSession,
    request: Request,
    uuid: str,
    body: CloseRequest,
) -> CloseResponse:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true")

    rec = await get_position(db, uuid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if rec.status == "closed":
        raise HTTPException(status_code=409, detail="Position already closed")

    # 按 strategy_instance 路由到对应 paper session
    state = request.app.state
    sess = None
    if rec.strategy_instance == "perp_basis_main":
        sess = getattr(state, "perp_basis_paper", None)
    elif rec.strategy_instance == "spot_perp_main":
        sess = getattr(state, "spot_perp_paper", None)
    else:  # funding_rate_main 走默认 paper_session
        sess = getattr(state, "paper_session", None)

    if sess is not None and hasattr(sess, "close_position"):
        await sess.close_position(uuid, reason=body.reason)
        msg = "close request submitted"
    else:
        msg = "paper session not running; DB record unchanged"

    return CloseResponse(uuid=uuid, status="closing", message=msg)
