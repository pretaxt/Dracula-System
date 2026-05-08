"""SpotPerpPaperSession — spot-perp 基差套利的 paper trading 会话。

开仓:
  scanner 检测到 |basis_pct| >= ENTRY_PCT 且当前持仓数 < MAX_CONCURRENT,
  写入 PositionRecord(strategy_instance="spot_perp_main"):
    target_apr_pct = signed basis_pct(开仓时基差,正=premium 负=discount)
    notional_usd = NOTIONAL
    notes = symbol
    fees_paid = 一次性预扣往返费

每次 tick(60s):
  对每个 open 仓位:
    current_basis = scanner 当前 basis_pct (若不在 opps 列表 = |basis|<threshold,近似 0)
    captured_pct = abs(entry_basis) - abs(current_basis)
    unrealized_pnl = captured_pct * notional / 100 - fees
  平仓条件:
    - basis 收敛(|current| <= EXIT_PCT)
    - 持仓时长 >= MAX_HOLD_HOURS
"""
from __future__ import annotations

import asyncio
import uuid as uuid_lib
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.logging import get_logger
from app.models.position import PositionRecord
from app.strategies.spot_perp_basis.runner import SpotPerpRunner

logger = get_logger(__name__)


# 默认参数 — 后续可移到 config/strategies/spot_perp_basis.yaml
ENTRY_PCT = Decimal("0.10")       # |basis_pct| >= 0.10% 入场
EXIT_PCT = Decimal("0.03")        # |basis_pct| <= 0.03% 收敛平仓
MAX_HOLD_HOURS = Decimal("12")    # 12 小时强制平仓
MAX_CONCURRENT = 3
NOTIONAL_PER_POSITION = Decimal("500")
ROUND_TRIP_FEE_USD = Decimal("0.50")  # 现货+永续两腿往返费,paper 简化


class SpotPerpPaperSession:
    """spot-perp 基差套利 paper trading 调度器。"""

    STRATEGY_INSTANCE = "spot_perp_main"
    STRATEGY_TYPE = "spot_perp"

    def __init__(
        self,
        runner: SpotPerpRunner,
        tick_interval_seconds: float = 60.0,
    ) -> None:
        self._runner = runner
        self._tick_interval = tick_interval_seconds
        self._stop_event = asyncio.Event()
        self._running = False
        self._last_tick_at: datetime | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_tick_at(self) -> datetime | None:
        return self._last_tick_at

    async def run_forever(self) -> None:
        self._running = True
        logger.info("spot_perp_paper_session_started", interval=self._tick_interval)
        try:
            while not self._stop_event.is_set():
                try:
                    async with get_session() as session:
                        await self._tick(session)
                    self._last_tick_at = datetime.now(timezone.utc)
                except Exception:
                    logger.exception("spot_perp_paper_tick_failed")

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._tick_interval
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False
            logger.info("spot_perp_paper_session_stopped")

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Core tick logic
    # ------------------------------------------------------------------

    async def _tick(self, session: AsyncSession) -> None:
        opps = self._runner.latest_opportunities or []
        opps_by_sym = {o.symbol: o for o in opps}

        stmt = (
            select(PositionRecord)
            .where(PositionRecord.strategy_instance == self.STRATEGY_INSTANCE)
            .where(PositionRecord.status == "open")
        )
        open_rows = (await session.execute(stmt)).scalars().all()
        existing_syms = {r.notes for r in open_rows}

        now = datetime.now(timezone.utc)

        closed_count = 0
        for r in open_rows:
            entry_basis = Decimal(str(r.target_apr_pct or 0))
            cur = opps_by_sym.get(r.notes)
            if cur is not None:
                current_basis = Decimal(str(cur.basis_pct))
            else:
                current_basis = Decimal("0")

            captured_pct = abs(entry_basis) - abs(current_basis)
            pnl = (
                captured_pct * (r.notional_usd or Decimal("0")) / Decimal("100")
                - (r.fees_paid or Decimal("0"))
            )
            r.unrealized_pnl = pnl

            held_seconds = (now - r.opened_at).total_seconds() if r.opened_at else 0.0
            held_hours = Decimal(str(held_seconds / 3600))

            should_close = False
            exit_reason: str | None = None
            if abs(current_basis) <= EXIT_PCT:
                should_close = True
                exit_reason = "basis_convergence"
            elif held_hours >= MAX_HOLD_HOURS:
                should_close = True
                exit_reason = "max_hold"

            if should_close:
                r.status = "closed"
                r.closed_at = now
                r.realized_pnl = pnl
                r.unrealized_pnl = Decimal("0")
                r.exit_reason = exit_reason
                closed_count += 1
                logger.info(
                    "spot_perp_paper_close",
                    symbol=r.notes,
                    entry_basis=str(entry_basis),
                    current_basis=str(current_basis),
                    realized=str(pnl),
                    reason=exit_reason,
                )

        slots = MAX_CONCURRENT - (len(open_rows) - closed_count)
        opened_count = 0
        if slots > 0:
            for opp in opps:
                if slots <= 0:
                    break
                if opp.symbol in existing_syms:
                    continue
                if abs(Decimal(str(opp.basis_pct))) < ENTRY_PCT:
                    continue

                new_pos = PositionRecord(
                    uuid=str(uuid_lib.uuid4()),
                    strategy_instance=self.STRATEGY_INSTANCE,
                    strategy_type=self.STRATEGY_TYPE,
                    status="open",
                    notional_usd=NOTIONAL_PER_POSITION,
                    margin_used=Decimal("0"),
                    target_apr_pct=Decimal(str(opp.basis_pct)),
                    realized_pnl=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                    funding_received=Decimal("0"),
                    fees_paid=ROUND_TRIP_FEE_USD,
                    opened_at=now,
                    closed_at=None,
                    exit_reason=None,
                    notes=opp.symbol,
                )
                session.add(new_pos)
                slots -= 1
                opened_count += 1
                logger.info(
                    "spot_perp_paper_open",
                    symbol=opp.symbol,
                    basis_pct=str(opp.basis_pct),
                    direction=opp.direction,
                    notional=str(NOTIONAL_PER_POSITION),
                )

        if closed_count > 0 or opened_count > 0:
            logger.info(
                "spot_perp_paper_tick_summary",
                opened=opened_count,
                closed=closed_count,
            )
        await session.commit()
