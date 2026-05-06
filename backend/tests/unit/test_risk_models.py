"""单元测试 — risk/models.py（领域数据类）"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.exchanges.models import InstrumentType, Side, Symbol
from app.risk.models import ExitReason, Position, PositionLeg, PositionStatus

BTC = Symbol("BTC", "USDT")


# ---------------------------------------------------------------------------
# PositionLeg
# ---------------------------------------------------------------------------


class TestPositionLeg:
    def _spot_leg(self, size="0.01", entry="60000", current=None) -> PositionLeg:
        return PositionLeg(
            exchange="binance",
            symbol=BTC,
            instrument_type=InstrumentType.SPOT,
            side=Side.BUY,
            size=Decimal(size),
            entry_price=Decimal(entry),
            current_price=Decimal(current) if current else None,
        )

    def _perp_leg(self, size="0.01", entry="60000", current=None) -> PositionLeg:
        return PositionLeg(
            exchange="binance",
            symbol=BTC,
            instrument_type=InstrumentType.PERPETUAL,
            side=Side.SELL,
            size=Decimal(size),
            entry_price=Decimal(entry),
            current_price=Decimal(current) if current else None,
        )

    def test_notional_usd(self):
        leg = self._spot_leg(size="0.01", entry="60000")
        assert leg.notional_usd == Decimal("600")

    def test_margin_used_no_leverage(self):
        leg = self._spot_leg(size="0.01", entry="60000")
        assert leg.margin_used == Decimal("600")

    def test_margin_used_with_leverage(self):
        leg = PositionLeg(
            exchange="binance",
            symbol=BTC,
            instrument_type=InstrumentType.PERPETUAL,
            side=Side.SELL,
            size=Decimal("0.01"),
            entry_price=Decimal("60000"),
            leverage=Decimal("2"),
        )
        assert leg.margin_used == Decimal("300")

    def test_unrealized_pnl_no_price(self):
        leg = self._spot_leg()
        assert leg.unrealized_pnl == Decimal("0")

    def test_unrealized_pnl_long_profit(self):
        leg = self._spot_leg(size="0.01", entry="60000", current="61000")
        # (61000 - 60000) * 0.01 = 10
        assert leg.unrealized_pnl == Decimal("10")

    def test_unrealized_pnl_long_loss(self):
        leg = self._spot_leg(size="0.01", entry="60000", current="59000")
        assert leg.unrealized_pnl == Decimal("-10")

    def test_unrealized_pnl_short_profit(self):
        # 空单：价格下跌盈利
        leg = self._perp_leg(size="0.01", entry="60000", current="59000")
        # -(59000 - 60000) * 0.01 = 10
        assert leg.unrealized_pnl == Decimal("10")

    def test_unrealized_pnl_short_loss(self):
        leg = self._perp_leg(size="0.01", entry="60000", current="61000")
        assert leg.unrealized_pnl == Decimal("-10")


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


class TestPosition:
    def _make(self, notional="500") -> Position:
        return Position(
            strategy_instance="funding_rate_main",
            symbol=BTC,
            notional_usd=Decimal(notional),
        )

    def _add_dn_legs(self, pos: Position, size="0.01", price="60000") -> None:
        """添加一对 Delta 中性腿（现货多 + 永续空）。"""
        pos.add_leg(PositionLeg(
            exchange="binance", symbol=BTC,
            instrument_type=InstrumentType.SPOT, side=Side.BUY,
            size=Decimal(size), entry_price=Decimal(price),
        ))
        pos.add_leg(PositionLeg(
            exchange="binance", symbol=BTC,
            instrument_type=InstrumentType.PERPETUAL, side=Side.SELL,
            size=Decimal(size), entry_price=Decimal(price),
        ))

    def test_id_is_uuid_format(self):
        pos = self._make()
        assert len(pos.id) == 36
        assert pos.id.count("-") == 4

    def test_two_positions_have_different_ids(self):
        assert self._make().id != self._make().id

    def test_initial_status_pending(self):
        pos = self._make()
        assert pos.status == PositionStatus.PENDING

    def test_is_open_when_pending(self):
        assert self._make().is_open is True

    def test_is_open_when_open(self):
        pos = self._make()
        pos.mark_open()
        assert pos.is_open is True

    def test_is_not_open_when_closed(self):
        pos = self._make()
        pos.mark_closed(ExitReason.MANUAL)
        assert pos.is_open is False

    def test_mark_open_sets_status(self):
        pos = self._make()
        pos.mark_open()
        assert pos.status == PositionStatus.OPEN

    def test_mark_open_sets_opened_at(self):
        pos = self._make()
        before = datetime.now(UTC)
        pos.mark_open()
        assert pos.opened_at is not None
        assert pos.opened_at >= before

    def test_mark_open_idempotent_timestamp(self):
        pos = self._make()
        pos.mark_open()
        first_ts = pos.opened_at
        pos.mark_open()
        assert pos.opened_at == first_ts  # 不覆盖已有时间戳

    def test_mark_closed_sets_all_fields(self):
        pos = self._make()
        pos.mark_open()
        pos.mark_closed(ExitReason.STOP_LOSS, realized_pnl=Decimal("-10"))
        assert pos.status == PositionStatus.CLOSED
        assert pos.exit_reason == ExitReason.STOP_LOSS
        assert pos.realized_pnl == Decimal("-10")
        assert pos.closed_at is not None

    def test_margin_used_sum_of_legs(self):
        pos = self._make()
        self._add_dn_legs(pos, size="0.01", price="60000")
        # 两条腿各 600 USD → 合计 1200
        assert pos.margin_used == Decimal("1200")

    def test_unrealized_pnl_no_current_price(self):
        pos = self._make()
        self._add_dn_legs(pos)
        assert pos.unrealized_pnl == Decimal("0")

    def test_total_pnl_includes_funding_minus_fees(self):
        pos = self._make()
        pos.mark_open()
        pos.funding_received = Decimal("5")
        pos.fees_paid = Decimal("1")
        # 0 + 0 + 5 - 1 = 4
        assert pos.total_pnl == Decimal("4")

    def test_holding_hours_zero_before_open(self):
        pos = self._make()
        assert pos.holding_hours == Decimal("0")

    def test_holding_hours_after_open(self):
        pos = self._make()
        pos.opened_at = datetime.now(UTC) - timedelta(hours=3)
        assert pos.holding_hours >= Decimal("3")

    def test_holding_hours_closed_uses_closed_at(self):
        pos = self._make()
        pos.opened_at = datetime(2026, 5, 6, 0, 0, 0, tzinfo=UTC)
        pos.closed_at = datetime(2026, 5, 6, 8, 0, 0, tzinfo=UTC)
        pos.status = PositionStatus.CLOSED
        assert pos.holding_hours == Decimal("8.0")

    def test_repr_contains_symbol_notional_status(self):
        pos = self._make("500")
        r = repr(pos)
        assert "BTC" in r
        assert "500" in r
        assert "pending" in r
