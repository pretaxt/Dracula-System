"""Orders 路由 — GET /orders

订单流水来源:
- 当前阶段没有真实 orders 表 (Week 6+ 在执行层实现)
- 暂时基于 PositionRecord 状态变动推断订单事件:
  - opened_at: 1 条开仓订单
  - closed_at: 1 条平仓订单
- exchange 从 PositionLegRecord 读取（真实腿数据）
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.orders import OrderOut, OrdersResponse
from app.models.position import PositionLegRecord, PositionRecord

router = APIRouter(prefix="/orders", tags=["orders"])

# 退出原因标签映射（存储值 → 中文展示）
_EXIT_REASON_LABEL: dict[str, str] = {
    "diff_decay":        "差价衰减",
    "diff_vanished":     "差价消失",
    "price_divergence":  "价格脱钩",
    "diff_declining":    "连续衰减",
    "max_hold":          "到期平仓",
    "max_hold_time":     "到期平仓",
    "manual":            "手动平仓",
    "stop_loss":         "止损",
    "basis_convergence": "基差收敛",
    "strategy":          "策略信号",
    "manual_cleanup_stale":     "清理过期",
    "manual_cleanup_imbalance": "清理失衡",
}


def _fmt_exit_reason(raw: str | None, side: str) -> str:
    """格式化平仓原因；开仓单返回 '—'。"""
    if side == "开仓":
        return "—"
    if not raw:
        return "—"
    label = _EXIT_REASON_LABEL.get(raw)
    if label:
        return label
    # 未知标签直接展示（英文 fallback）
    return raw


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

    # 批量加载 legs（获取真实交易所信息）
    pos_ids = [r.id for r in rows]
    legs_by_pos: dict[int, list[PositionLegRecord]] = {}
    if pos_ids:
        leg_result = await db.execute(
            select(PositionLegRecord).where(PositionLegRecord.position_id.in_(pos_ids))
        )
        for lr in leg_result.scalars().all():
            legs_by_pos.setdefault(lr.position_id, []).append(lr)

    orders: list[OrderOut] = []
    for row in rows:
        symbol = (row.notes or "").split("\n", 1)[0]
        notional_str = str(row.notional_usd)

        # 交易所：从 legs 提取；跨所展示 "多→空"；无 legs 则按策略类型推断
        legs = legs_by_pos.get(row.id, [])
        if legs:
            buy_legs  = [lg for lg in legs if lg.side == "buy"]
            sell_legs = [lg for lg in legs if lg.side == "sell"]
            if buy_legs and sell_legs:
                exchange_str = f"{buy_legs[0].exchange}→{sell_legs[0].exchange}"
            elif buy_legs:
                exchange_str = buy_legs[0].exchange
            elif sell_legs:
                exchange_str = sell_legs[0].exchange
            else:
                exchange_str = legs[0].exchange
        else:
            st = row.strategy_type or ""
            exchange_str = {
                "perp_basis":   "跨所",
                "spot_perp":    "binance",
                "funding_rate": "binance",
            }.get(st, "binance")

        # 已实现盈亏（仅平仓时有意义）
        pnl_val = row.realized_pnl if row.realized_pnl is not None else None
        pnl_str = f"{float(pnl_val):+.4f}" if pnl_val is not None else "—"

        if row.opened_at is not None:
            orders.append(
                OrderOut(
                    time=row.opened_at,
                    exchange=exchange_str,
                    symbol=symbol,
                    order_type="MARKET",
                    side="开仓",
                    amount=notional_str,
                    pnl="—",
                    status="filled",
                    position_uuid=str(row.uuid),
                    exit_reason="—",
                )
            )
        if row.closed_at is not None:
            orders.append(
                OrderOut(
                    time=row.closed_at,
                    exchange=exchange_str,
                    symbol=symbol,
                    order_type="MARKET",
                    side="平仓",
                    amount=notional_str,
                    pnl=pnl_str,
                    status="filled",
                    position_uuid=str(row.uuid),
                    exit_reason=_fmt_exit_reason(row.exit_reason, "平仓"),
                )
            )

    orders.sort(key=lambda o: o.time, reverse=True)
    orders = orders[:limit]
    return OrdersResponse(data=orders, total=len(orders))
