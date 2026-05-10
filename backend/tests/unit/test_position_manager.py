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


class TestLegRoundTrip:
    """X5 修复 — legs 必须能在 save → load 之间往返，否则重启后无法平仓。"""

    def test_position_leg_record_from_to_domain_roundtrip(self):
        """直接测 PositionLegRecord 转换不丢字段。"""
        from app.exchanges.models import InstrumentType, Side
        from app.models.position import PositionLegRecord
        leg = PositionLeg(
            exchange="binance",
            symbol=BTC,
            instrument_type=InstrumentType.SPOT,
            side=Side.BUY,
            size=Decimal("0.5"),
            entry_price=Decimal("60000"),
            leverage=Decimal("1"),
        )
        rec = PositionLegRecord.from_domain(leg, position_id=42)
        assert rec.position_id == 42
        assert rec.exchange == "binance"
        assert rec.symbol == "BTC/USDT"
        assert rec.instrument_type == "spot"
        assert rec.side == "buy"
        assert rec.size == Decimal("0.5")
        assert rec.entry_price == Decimal("60000")
        assert rec.margin == Decimal("30000")  # notional / leverage
        # round trip
        restored = rec.to_domain()
        assert restored.exchange == leg.exchange
        assert str(restored.symbol) == str(leg.symbol)
        assert restored.instrument_type == leg.instrument_type
        assert restored.side == leg.side
        assert restored.size == leg.size
        assert restored.entry_price == leg.entry_price
        assert restored.leverage == leg.leverage

    def test_perp_short_leg_with_leverage(self):
        from app.exchanges.models import InstrumentType, Side
        from app.models.position import PositionLegRecord
        leg = PositionLeg(
            exchange="binance",
            symbol=BTC,
            instrument_type=InstrumentType.PERPETUAL,
            side=Side.SELL,
            size=Decimal("0.5"),
            entry_price=Decimal("60000"),
            leverage=Decimal("5"),
        )
        rec = PositionLegRecord.from_domain(leg, position_id=99)
        assert rec.margin == Decimal("6000")  # 30000 / 5
        restored = rec.to_domain()
        assert restored.leverage == Decimal("5")
        assert restored.side == Side.SELL


class TestDiscard:
    """discard() 用于 broker 失败时回滚未成功开仓的 position。

    Why: 旧实现把内存 position 留在 _positions 字典里，造成幽灵持仓
    占用 max_positions 配额。
    """

    def test_discard_removes_from_all_positions(self):
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))
        assert mgr.discard(pos.id) is True
        assert mgr.all_positions == []

    def test_discard_unknown_id_returns_false(self):
        mgr = PositionManager()
        assert mgr.discard("does-not-exist") is False

    def test_discard_idempotent(self):
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))
        mgr.discard(pos.id)
        assert mgr.discard(pos.id) is False  # 第二次返回 False

    def test_discard_does_not_affect_other_positions(self):
        mgr = PositionManager()
        keep = mgr.create("s1", BTC, Decimal("500"))
        drop = mgr.create("s1", ETH, Decimal("500"))
        mgr.discard(drop.id)
        assert mgr.all_positions == [keep]

    def test_discard_frees_max_positions_quota(self):
        """关键：discard 后总持仓数 -1，max_positions=1 配额可被复用。"""
        mgr = PositionManager()
        pos = mgr.create("s1", BTC, Decimal("500"))
        pos.mark_open()
        assert len(mgr.open_positions) == 1
        mgr.discard(pos.id)
        assert len(mgr.open_positions) == 0


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

            # select(id by uuid) → 返回 None（首次 save，不存在）
            scalar_result = MagicMock()
            scalar_result.scalar_one_or_none = MagicMock(return_value=None)

            async def fake_execute(stmt):
                return scalar_result

            async def fake_merge(record):
                merged.append(record)
                # mimic SA flushed PK
                if record.id is None:
                    record.id = 1
                return record

            async def fake_flush():
                return None

            session.execute = fake_execute
            session.merge = fake_merge
            session.flush = fake_flush
            session.add = MagicMock()
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
