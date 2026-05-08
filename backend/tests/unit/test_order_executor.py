"""单元测试 — execution/order_executor.py"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.exchanges.models import FundingRate, InstrumentType, OrderBook, Side, Symbol
from app.execution.order_executor import OrderExecutor
from app.execution.paper_broker import PaperBroker
from app.risk.limits import RiskGuard, RiskLimitError, RiskLimits
from app.risk.models import ExitReason, PositionStatus
from app.risk.position_manager import PositionManager
from app.strategies.funding_rate.scanner import FundingRateOpportunity

BTC = Symbol("BTC", "USDT")
ETH = Symbol("ETH", "USDT")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_opportunity(
    symbol: Symbol = BTC,
    exchange: str = "binance",
    rate: str = "0.0003",   # 年化约 26%
    spot_ask: str = "60000",
    perp_bid: str = "60010",
) -> FundingRateOpportunity:
    funding = FundingRate(
        symbol=symbol,
        exchange=exchange,
        rate=Decimal(rate),
        next_funding_time=1_700_000_000_000,
        funding_interval_hours=8,
    )
    spot_ob = OrderBook(
        symbol=symbol,
        bids=[(Decimal(spot_ask) - Decimal("10"), Decimal("1"))],
        asks=[(Decimal(spot_ask), Decimal("1"))],
        timestamp=1_700_000_000_000,
    )
    perp_ob = OrderBook(
        symbol=symbol,
        bids=[(Decimal(perp_bid), Decimal("1"))],
        asks=[(Decimal(perp_bid) + Decimal("10"), Decimal("1"))],
        timestamp=1_700_000_000_000,
    )
    return FundingRateOpportunity(
        symbol=symbol,
        exchange=exchange,
        funding_rate=funding,
        spot_orderbook=spot_ob,
        perp_orderbook=perp_ob,
    )


def _default_limits(**overrides) -> RiskLimits:
    limits = RiskLimits(
        max_positions=5,
        max_total_notional_usd=Decimal("50000"),
        max_position_size_usd=Decimal("10000"),
        min_position_size_usd=Decimal("10"),
        stop_loss_pct=Decimal("5.0"),
        max_hold_hours=Decimal("168"),
        min_apr_pct=Decimal("10.0"),
    )
    for k, v in overrides.items():
        setattr(limits, k, v)
    return limits


def _make_executor(
    slippage_bps: str = "0",
    **limit_overrides,
) -> tuple[OrderExecutor, PositionManager]:
    broker = PaperBroker(slippage_bps=Decimal(slippage_bps), fee_rate=Decimal("0.001"))
    manager = PositionManager()
    guard = RiskGuard(limits=_default_limits(**limit_overrides))
    executor = OrderExecutor(broker=broker, manager=manager, guard=guard)
    return executor, manager


# ---------------------------------------------------------------------------
# open_delta_neutral — 开仓流程
# ---------------------------------------------------------------------------


class TestOpenDeltaNeutral:
    @pytest.mark.asyncio
    async def test_returns_open_position(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        assert pos.status == PositionStatus.OPEN

    @pytest.mark.asyncio
    async def test_position_has_two_legs(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        assert len(pos.legs) == 2

    @pytest.mark.asyncio
    async def test_legs_have_correct_sides(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        sides = {leg.side for leg in pos.legs}
        assert Side.BUY in sides
        assert Side.SELL in sides

    @pytest.mark.asyncio
    async def test_legs_have_correct_instrument_types(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        types = {leg.instrument_type for leg in pos.legs}
        assert InstrumentType.SPOT in types
        assert InstrumentType.PERPETUAL in types

    @pytest.mark.asyncio
    async def test_fees_are_recorded(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        assert pos.fees_paid > Decimal("0")

    @pytest.mark.asyncio
    async def test_position_added_to_open_positions(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        assert pos in manager.open_positions

    @pytest.mark.asyncio
    async def test_save_is_called_once(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()
        mock_save = AsyncMock()

        with patch.object(manager, "save", new=mock_save):
            await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        mock_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_quantity_calculated_from_spot_ask(self):
        """数量 = size_usd / spot_ask（无滑点时精确整除）。"""
        executor, manager = _make_executor(slippage_bps="0")
        opp = _make_opportunity(spot_ask="60000")

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        # 600 / 60000 = 0.01
        spot_leg = next(l for l in pos.legs if l.side == Side.BUY)
        assert spot_leg.size == Decimal("0.01000000")

    @pytest.mark.asyncio
    async def test_spot_entry_price_equals_ask_at_zero_slippage(self):
        executor, manager = _make_executor(slippage_bps="0")
        opp = _make_opportunity(spot_ask="60000")

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        spot_leg = next(l for l in pos.legs if l.side == Side.BUY)
        assert spot_leg.entry_price == Decimal("60000.00000000")

    @pytest.mark.asyncio
    async def test_perp_entry_price_equals_bid_at_zero_slippage(self):
        executor, manager = _make_executor(slippage_bps="0")
        opp = _make_opportunity(perp_bid="60010")

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        perp_leg = next(l for l in pos.legs if l.side == Side.SELL)
        assert perp_leg.entry_price == Decimal("60010.00000000")

    @pytest.mark.asyncio
    async def test_notional_usd_matches_size_usd(self):
        """无滑点时，现货腿名义价值应接近 size_usd。"""
        executor, manager = _make_executor(slippage_bps="0")
        opp = _make_opportunity(spot_ask="60000")

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        spot_leg = next(l for l in pos.legs if l.side == Side.BUY)
        assert spot_leg.notional_usd == Decimal("600.00000000")


# ---------------------------------------------------------------------------
# open_delta_neutral — 风控拦截
# ---------------------------------------------------------------------------


class TestOpenRiskBlocked:
    @pytest.mark.asyncio
    async def test_raises_risk_limit_error_when_max_positions_exceeded(self):
        executor, _ = _make_executor(max_positions=0)
        opp = _make_opportunity()

        with pytest.raises(RiskLimitError):
            await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

    @pytest.mark.asyncio
    async def test_no_position_created_on_risk_error(self):
        executor, manager = _make_executor(max_positions=0)
        opp = _make_opportunity()

        try:
            await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
        except RiskLimitError:
            pass

        assert manager.all_positions == []

    @pytest.mark.asyncio
    async def test_raises_when_size_below_minimum(self):
        executor, _ = _make_executor(min_position_size_usd=Decimal("500"))
        opp = _make_opportunity()

        with pytest.raises(RiskLimitError):
            await executor.open_delta_neutral(opp, size_usd=Decimal("100"))

    @pytest.mark.asyncio
    async def test_raises_when_size_above_maximum(self):
        executor, _ = _make_executor(max_position_size_usd=Decimal("200"))
        opp = _make_opportunity()

        with pytest.raises(RiskLimitError):
            await executor.open_delta_neutral(opp, size_usd=Decimal("500"))


# ---------------------------------------------------------------------------
# close_position — 平仓流程
# ---------------------------------------------------------------------------


class TestClosePosition:
    @pytest.mark.asyncio
    async def test_position_is_closed_after_call(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
            await executor.close_position(pos.id)

        assert pos.status == PositionStatus.CLOSED

    @pytest.mark.asyncio
    async def test_exit_reason_is_set(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
            await executor.close_position(pos.id, reason=ExitReason.FUNDING_REVERSAL)

        assert pos.exit_reason == ExitReason.FUNDING_REVERSAL

    @pytest.mark.asyncio
    async def test_default_reason_is_manual(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
            await executor.close_position(pos.id)

        assert pos.exit_reason == ExitReason.MANUAL

    @pytest.mark.asyncio
    async def test_close_fees_accumulate(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
            fees_after_open = pos.fees_paid
            await executor.close_position(pos.id)

        assert pos.fees_paid > fees_after_open

    @pytest.mark.asyncio
    async def test_position_removed_from_open_positions(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
            await executor.close_position(pos.id)

        assert pos not in manager.open_positions

    @pytest.mark.asyncio
    async def test_returns_position_object(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
            result = await executor.close_position(pos.id)

        assert result is pos

    @pytest.mark.asyncio
    async def test_save_called_twice_total(self):
        """open + close 各调用一次 save，共 2 次。"""
        executor, manager = _make_executor()
        opp = _make_opportunity()
        mock_save = AsyncMock()

        with patch.object(manager, "save", new=mock_save):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
            await executor.close_position(pos.id)

        assert mock_save.await_count == 2

    @pytest.mark.asyncio
    async def test_unknown_position_id_raises_key_error(self):
        executor, _ = _make_executor()

        with pytest.raises(KeyError):
            await executor.close_position("nonexistent-id")

    @pytest.mark.asyncio
    async def test_realized_pnl_zero_at_zero_slippage_same_prices(self):
        """无滑点 + 开平仓参考价一致时，realized_pnl = 0。"""
        executor, manager = _make_executor(slippage_bps="0")
        opp = _make_opportunity(spot_ask="60000", perp_bid="60000")

        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
            await executor.close_position(pos.id)

        assert pos.realized_pnl == Decimal("0")


# ---------------------------------------------------------------------------
# check_all_positions — 持仓监控
# ---------------------------------------------------------------------------


class TestCheckAllPositions:
    def test_no_positions_returns_empty(self):
        executor, _ = _make_executor()
        assert executor.check_all_positions() == []

    def test_healthy_position_not_flagged(self):
        executor, manager = _make_executor()
        pos = manager.create("s1", BTC, Decimal("500"))
        pos.mark_open()

        assert executor.check_all_positions() == []

    def test_stop_loss_violated_position_is_flagged(self):
        executor, manager = _make_executor(stop_loss_pct=Decimal("1.0"))
        pos = manager.create("s1", BTC, Decimal("500"))
        pos.mark_open()
        pos.realized_pnl = Decimal("-10")  # 亏损 2% > 1%

        flagged = executor.check_all_positions()

        assert len(flagged) == 1
        assert flagged[0][0] is pos

    def test_closed_positions_are_not_checked(self):
        executor, manager = _make_executor(stop_loss_pct=Decimal("0.0"))
        pos = manager.create("s1", BTC, Decimal("500"))
        pos.mark_open()
        pos.realized_pnl = Decimal("-999")
        pos.mark_closed(ExitReason.MANUAL)

        assert executor.check_all_positions() == []

    def test_violations_are_returned_alongside_position(self):
        executor, manager = _make_executor(stop_loss_pct=Decimal("0.5"))
        pos = manager.create("s1", BTC, Decimal("500"))
        pos.mark_open()
        pos.realized_pnl = Decimal("-5")  # 亏损 1% > 0.5%

        flagged = executor.check_all_positions()
        _, violations = flagged[0]

        assert any(v.rule == "stop_loss_pct" for v in violations)

    def test_only_violated_positions_are_returned(self):
        executor, manager = _make_executor(stop_loss_pct=Decimal("1.0"))

        healthy = manager.create("s1", BTC, Decimal("500"))
        healthy.mark_open()

        bad = manager.create("s1", ETH, Decimal("300"))
        bad.mark_open()
        bad.realized_pnl = Decimal("-10")  # 超过 1% 止损

        flagged = executor.check_all_positions()

        assert len(flagged) == 1
        assert flagged[0][0] is bad

    def test_multiple_violated_positions_all_returned(self):
        executor, manager = _make_executor(stop_loss_pct=Decimal("0.5"))

        for _ in range(3):
            pos = manager.create("s1", BTC, Decimal("200"))
            pos.mark_open()
            pos.realized_pnl = Decimal("-5")  # 超过 0.5% 止损

        flagged = executor.check_all_positions()
        assert len(flagged) == 3


# ---------------------------------------------------------------------------
# perp_leverage 写入 PositionLeg
# ---------------------------------------------------------------------------


def _make_executor_with_leverage(
    perp_leverage: Decimal,
) -> tuple[OrderExecutor, PositionManager]:
    broker = PaperBroker(slippage_bps=Decimal("0"), fee_rate=Decimal("0"))
    manager = PositionManager()
    guard = RiskGuard(limits=_default_limits())
    executor = OrderExecutor(
        broker=broker, manager=manager, guard=guard,
        perp_leverage=perp_leverage,
    )
    return executor, manager


class TestPerpLeverage:
    @pytest.mark.asyncio
    async def test_default_perp_leverage_is_1(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()
        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
        perp_leg = next(l for l in pos.legs
                        if l.instrument_type == InstrumentType.PERPETUAL)
        assert perp_leg.leverage == Decimal("1")

    @pytest.mark.asyncio
    async def test_5x_leverage_propagates_to_perp_leg(self):
        executor, manager = _make_executor_with_leverage(Decimal("5"))
        opp = _make_opportunity()
        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
        perp_leg = next(l for l in pos.legs
                        if l.instrument_type == InstrumentType.PERPETUAL)
        assert perp_leg.leverage == Decimal("5")

    @pytest.mark.asyncio
    async def test_3x_leverage_propagates_to_perp_leg(self):
        executor, manager = _make_executor_with_leverage(Decimal("3"))
        opp = _make_opportunity()
        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
        perp_leg = next(l for l in pos.legs
                        if l.instrument_type == InstrumentType.PERPETUAL)
        assert perp_leg.leverage == Decimal("3")

    @pytest.mark.asyncio
    async def test_spot_leg_leverage_unchanged_at_1(self):
        """spot 没有杠杆概念——应保持默认 1，不受 perp_leverage 参数影响。"""
        executor, manager = _make_executor_with_leverage(Decimal("5"))
        opp = _make_opportunity()
        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
        spot_leg = next(l for l in pos.legs
                        if l.instrument_type == InstrumentType.SPOT)
        assert spot_leg.leverage == Decimal("1")

    @pytest.mark.asyncio
    async def test_5x_leverage_makes_perp_margin_used_one_fifth_notional(self):
        """5x 杠杆：perp_leg.margin_used = notional / 5。"""
        executor, manager = _make_executor_with_leverage(Decimal("5"))
        opp = _make_opportunity()
        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("500"))
        perp_leg = next(l for l in pos.legs
                        if l.instrument_type == InstrumentType.PERPETUAL)
        # margin_used = notional / leverage = ~500 / 5 = ~100
        # （受滑点和成交价影响略有浮动；用 5x 判断 margin < 1/4 notional）
        assert perp_leg.margin_used < perp_leg.notional_usd / Decimal("4")
        assert perp_leg.margin_used > perp_leg.notional_usd / Decimal("6")
