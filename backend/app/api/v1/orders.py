"""Orders 路由 — GET /orders

订单流水来源:
- 当前阶段没有真实 orders 表 (Week 6+ 在执行层实现)
- 暂时基于 PositionRecord 状态变动推断订单事件:
  - opened_at: 1 条开仓订单
  - closed_at: 1 条平仓订单
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.orders import OrderOut, OrdersResponse
from app.models.position import PositionRecord

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=OrdersResponse)
async def list_orders(
    _: CurrentUser,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> OrdersResponse:
    """返回最近的开/平仓事件作为订单流水。"""
    stmt = (
        select(PositionRecord)
        .order_by(PositionRecord.opened_at.desc().nulls_last())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()

    orders: list[OrderOut] = []
    for row in rows:
        symbol = row.notes or ""
        notional_str = str(row.notional_usd)
        if row.opened_at is not None:
            orders.append(
                OrderOut(
                    time=row.opened_at,
                    exchange="binance",
                    symbol=symbol,
                    order_type="LIMIT",
                    side="hedge_open",
                    amount=notional_str,
                    price="market",
                    status="filled",
                    position_uuid=row.uuid,
                )
            )
        if row.closed_at is not None:
            orders.append(
                OrderOut(
                    time=row.closed_at,
                    exchange="binance",
                    symbol=symbol,
                    order_type="LIMIT",
                    side="hedge_close",
                    amount=notional_str,
                    price="market",
                    status="filled",
                    position_uuid=row.uuid,
                )
            )

    orders.sort(key=lambda o: o.time, reverse=True)
    orders = orders[:limit]
    return OrdersResponse(data=orders, total=len(orders))
