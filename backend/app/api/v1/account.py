"""Account 路由 — GET /account/balance

当前阶段:
- 没有真实账户余额聚合服务 (Phase 1+ 调 CCXT fetch_balance)
- 估算: total_equity = INITIAL_CAPITAL_USD + 累计净 PnL
- positions_notional = SUM(notional_usd WHERE status=open)
- locked = SUM(margin_used WHERE status=open)
- available = total_equity - locked
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.account import AccountBalance
from app.models.position import PositionRecord
from app.services.dashboard_service import INITIAL_CAPITAL_USD

router = APIRouter(prefix="/account", tags=["account"])


@router.get("/balance", response_model=AccountBalance)
async def get_balance(_: CurrentUser, db: DbSession) -> AccountBalance:
    """聚合估算账户余额。"""
    realized = (
        await db.execute(
            select(func.coalesce(func.sum(PositionRecord.realized_pnl), 0))
        )
    ).scalar_one()

    unrealized = (
        await db.execute(
            select(func.coalesce(func.sum(PositionRecord.unrealized_pnl), 0)).where(
                PositionRecord.status == "open"
            )
        )
    ).scalar_one()

    notional_open = (
        await db.execute(
            select(func.coalesce(func.sum(PositionRecord.notional_usd), 0)).where(
                PositionRecord.status == "open"
            )
        )
    ).scalar_one()

    margin_locked = (
        await db.execute(
            select(func.coalesce(func.sum(PositionRecord.margin_used), 0)).where(
                PositionRecord.status == "open"
            )
        )
    ).scalar_one()

    realized_d = Decimal(str(realized))
    unrealized_d = Decimal(str(unrealized))
    notional_d = Decimal(str(notional_open))
    locked_d = Decimal(str(margin_locked))

    total_equity = INITIAL_CAPITAL_USD + realized_d + unrealized_d
    available = total_equity - locked_d if total_equity > locked_d else Decimal("0")

    return AccountBalance(
        total_equity_usd=str(round(total_equity, 2)),
        available_usd=str(round(available, 2)),
        locked_usd=str(round(locked_d, 2)),
        positions_notional_usd=str(round(notional_d, 2)),
        realized_pnl_usd=str(round(realized_d, 8)),
        unrealized_pnl_usd=str(round(unrealized_d, 8)),
        currency="USDT",
        updated_at=datetime.now(timezone.utc),
    )
