"""单元测试 — risk/position_manager.py"""
from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exchanges.models import InstrumentType, Side, Symbol
from app.risk.models import ExitReason, Position, PositionLeg, PositionStatus
from app.risk.position_manager import PositionManager

BTC = Symbol("BTC", "USDT")
ETH = Symbol("ETH", "USDT")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _fake_session() -> AsyncGenerator[MagicMock, None]:
    session = MagicMock()
    session.merge = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    ))
    yield session


def _make_leg(symbol: Symbol = BTC, side: Side = Side.BUY) -> PositionLeg:
    return PositionLeg(
        exchange="binance",
        symbol=symbol,
        instrument_type=InstrumentType.SPOT if side == Side.BUY else InstrumentType.PERPETUAL,
        side=side,
        size=Decimal("0.01"),
        entry_price=Decimal("60000"),
    )


# ---------------------------------------------------------------------------
# 创建与查询
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_returns_pending_position(self):
        mgr = PositionManager()
        pos = mgr.create("funding_rate_main", BTC, Decimal("500"))
        assert pos.status == PositionStatus.PENDING
        assert pos.symbol == BTC
        assert pos.notional_usd == Decimal("500")

    def test_create_registers_in_all_positions(self):
        mgr = PositionManager()
        pos = mgr.create("funding_rate_main", BTC, Decimal("500"))
        assert pos in mgr.all_positions

    def test_create_with_legs_attaches_them(self):
        mgr = PositionManager()
        legs = [_make_leg(BTC, Side.BUY), _make_leg(BTC, Side.SELL)]
        pos = mgr.create("funding_rate_main", BTC, Decimal("500"), legs=legs)
        assert len(pos.legs) == 2

    def test_create_multiple_positions(self):
        mgr = PositionManager()
        mgr.create("s1", BTC, Decimal("500"))
        mgr.create("s1", ETH, Decimal("300"))
        assert len(mgr.all_positions) == 2

    def test_get_returns_position_by_id(self):
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))
        assert mgr.get(pos.id) is pos

    def test_get_returns_none_for_unknown_id(self):
        mgr = PositionManager()
        assert mgr.get("nonexistent-id") is None


class TestQueries:
    def test_open_positions_excludes_closed(self):
        mgr = PositionManager()
        open_pos = mgr.create("s1", BTC, Decimal("500"))
        open_pos.mark_open()
        closed_pos = mgr.create("s1", ETH, Decimal("300"))
        closed_pos.mark_open()
        closed_pos.mark_closed(ExitReason.MANUAL)

        assert open_pos in mgr.open_positions
        assert closed_pos not in mgr.open_positions

    def test_total_notional_sums_only_open(self):
        mgr = PositionManager()
        p1 = mgr.create("s1", BTC, Decimal("500"))
        p1.mark_open()
        p2 = mgr.create("s1", ETH, Decimal("300"))
        p2.mark_open()
        p2.mark_closed(ExitReason.MANUAL)

        assert mgr.total_notional_usd == Decimal("500")

    def test_total_notional_empty_is_zero(self):
        mgr = PositionManager()
        assert mgr.total_notional_usd == Decimal("0")

    def test_get_by_symbol_filters_correctly(self):
        mgr = PositionManager()
        btc_pos = mgr.create("s1", BTC, Decimal("500"))
        btc_pos.mark_open()
        eth_pos = mgr.create("s1", ETH, Decimal("300"))
        eth_pos.mark_open()

        results = mgr.get_by_symbol(BTC)
        assert btc_pos in results
        assert eth_pos not in results


# ---------------------------------------------------------------------------
# 更新操作
# ---------------------------------------------------------------------------


class TestUpdates:
    def test_record_funding_accumulates(self):
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))
        mgr.record_funding(pos.id, Decimal("2"))
        mgr.record_funding(pos.id, Decimal("3"))
        assert pos.funding_received == Decimal("5")

    def test_record_fees_accumulates(self):
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))
        mgr.record_fees(pos.id, Decimal("1"))
        mgr.record_fees(pos.id, Decimal("0.5"))
        assert pos.fees_paid == Decimal("1.5")

    def test_close_marks_position_closed(self):
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))
        pos.mark_open()
        mgr.close(pos.id, ExitReason.FUNDING_REVERSAL, realized_pnl=Decimal("10"))
        assert pos.status == PositionStatus.CLOSED
        assert pos.exit_reason == ExitReason.FUNDING_REVERSAL
        assert pos.realized_pnl == Decimal("10")

    def test_close_returns_position(self):
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))
        returned = mgr.close(pos.id, ExitReason.MANUAL)
        assert returned is pos

    def test_close_unknown_id_raises_key_error(self):
        mgr = PositionManager()
        with pytest.raises(KeyError):
            mgr.close("bad-id", ExitReason.MANUAL)

    def test_record_funding_unknown_id_raises_key_error(self):
        mgr = PositionManager()
        with pytest.raises(KeyError):
            mgr.record_funding("bad-id", Decimal("1"))

    def test_record_fees_unknown_id_raises_key_error(self):
        mgr = PositionManager()
        with pytest.raises(KeyError):
            mgr.record_fees("bad-id", Decimal("1"))


# ---------------------------------------------------------------------------
# DB 持久化（mock session）
# ---------------------------------------------------------------------------


class TestPersistence:
    @pytest.mark.asyncio
    async def test_save_calls_session_merge(self):
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))

        merged = []

        @asynccontextmanager
        async def capturing_session() -> AsyncGenerator[MagicMock, None]:
            session = MagicMock()

            async def capture(record):
                merged.append(record)

            session.merge = capture
            yield session

        with patch("app.core.database.get_session", new=capturing_session):
            await mgr.save(pos)

        assert len(merged) == 1
        assert merged[0].uuid == pos.id

    @pytest.mark.asyncio
    async def test_save_db_failure_does_not_raise(self):
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))

        @asynccontextmanager
        async def failing_session() -> AsyncGenerator[MagicMock, None]:
            raise RuntimeError("db down")
            yield  # pragma: no cover

        with patch("app.core.database.get_session", new=failing_session):
            await mgr.save(pos)  # 不应抛出

    @pytest.mark.asyncio
    async def test_load_open_positions_empty_db_returns_zero(self):
        mgr = PositionManager()
        with patch("app.core.database.get_session", new=_fake_session):
            count = await mgr.load_open_positions()
        assert count == 0
        assert mgr.all_positions == []

    @pytest.mark.asyncio
    async def test_load_open_positions_db_failure_returns_zero(self):
        mgr = PositionManager()

        @asynccontextmanager
        async def failing_session() -> AsyncGenerator[MagicMock, None]:
            raise RuntimeError("db down")
            yield  # pragma: no cover

        with patch("app.core.database.get_session", new=failing_session):
            count = await mgr.load_open_positions()

        assert count == 0
