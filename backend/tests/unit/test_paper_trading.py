"""单元测试 — strategies/funding_rate/paper_trading.py"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exchanges.models import FundingRate, OrderBook, Symbol
from app.execution.order_executor import OrderExecutor
from app.execution.paper_broker import PaperBroker
from app.risk.limits import RiskGuard, RiskLimits
from app.risk.models import ExitReason
from app.risk.position_manager import PositionManager
from app.strategies.funding_rate.paper_trading import PaperTradingSession, StatusSnapshot
from app.strategies.funding_rate.scanner import FundingRateOpportunity, FundingRateScanner

BTC = Symbol("BTC", "USDT")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_opportunity(
    rate: str = "0.0003",
    spot_ask: str = "60000",
    perp_bid: str = "60010",
) -> FundingRateOpportunity:
    funding = FundingRate(
        symbol=BTC, exchange="binance",
        rate=Decimal(rate),
        next_funding_time=1_700_000_000_000,
        funding_interval_hours=8,
    )
    spot_ob = OrderBook(
        symbol=BTC,
        bids=[(Decimal(spot_ask) - Decimal("10"), Decimal("10"))],
        asks=[(Decimal(spot_ask), Decimal("10"))],
        timestamp=1_700_000_000_000,
    )
    perp_ob = OrderBook(
        symbol=BTC,
        bids=[(Decimal(perp_bid), Decimal("10"))],
        asks=[(Decimal(perp_bid) + Decimal("10"), Decimal("10"))],
        timestamp=1_700_000_000_000,
    )
    return FundingRateOpportunity(
        symbol=BTC, exchange="binance",
        funding_rate=funding,
        spot_orderbook=spot_ob,
        perp_orderbook=perp_ob,
    )


def _make_session(
    opportunities: list[FundingRateOpportunity] | None = None,
    max_positions: int = 3,
    stop_loss_pct: str = "5",
    max_hold_hours: str = "720",
    fee_rate: str = "0",
    slippage_bps: str = "0",
) -> PaperTradingSession:
    mock_scanner = MagicMock(spec=FundingRateScanner)
    mock_scanner.scan = AsyncMock(return_value=opportunities or [])

    broker = PaperBroker(
        slippage_bps=Decimal(slippage_bps),
        fee_rate=Decimal(fee_rate),
    )
    manager = PositionManager()
    manager.save = AsyncMock(return_value=None)  # type: ignore[method-assign]

    limits = RiskLimits(
        max_positions=max_positions,
        max_total_notional_usd=Decimal("50000"),
        max_position_size_usd=Decimal("10000"),
        min_position_size_usd=Decimal("10"),
        stop_loss_pct=Decimal(stop_loss_pct),
        max_hold_hours=Decimal(max_hold_hours),
        min_apr_pct=Decimal("5"),
    )
    guard = RiskGuard(limits=limits)
    executor = OrderExecutor(broker=broker, manager=manager, guard=guard)

    return PaperTradingSession(
        scanner=mock_scanner,
        executor=executor,
        manager=manager,
        size_per_trade_usd=Decimal("500"),
    )


# ---------------------------------------------------------------------------
# run_once — 基础行为
# ---------------------------------------------------------------------------


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_returns_opportunities_from_scanner(self):
        session = _make_session(opportunities=[_make_opportunity()])
        result = await session.run_once()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_opportunities(self):
        session = _make_session(opportunities=[])
        assert await session.run_once() == []

    @pytest.mark.asyncio
    async def test_opens_position_when_opportunity_found(self):
        session = _make_session(opportunities=[_make_opportunity()])
        await session.run_once()
        assert len(session._manager.all_positions) >= 1

    @pytest.mark.asyncio
    async def test_no_position_when_no_opportunities(self):
        session = _make_session(opportunities=[])
        await session.run_once()
        assert session._manager.all_positions == []

    @pytest.mark.asyncio
    async def test_scanner_called_once_per_tick(self):
        session = _make_session(opportunities=[_make_opportunity()])
        await session.run_once()
        session._scanner.scan.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tick_count_increments_each_call(self):
        session = _make_session(opportunities=[])
        assert session._tick_count == 0
        await session.run_once()
        assert session._tick_count == 1
        await session.run_once()
        assert session._tick_count == 2

    @pytest.mark.asyncio
    async def test_scanner_exception_does_not_raise(self):
        session = _make_session()
        session._scanner.scan = AsyncMock(side_effect=RuntimeError("network error"))
        result = await session.run_once()
        assert result == []


# ---------------------------------------------------------------------------
# 风控拦截
# ---------------------------------------------------------------------------


class TestRiskBlocking:
    @pytest.mark.asyncio
    async def test_max_positions_prevents_duplicate_open(self):
        session = _make_session(
            opportunities=[_make_opportunity()], max_positions=1
        )
        await session.run_once()
        assert len(session._manager.open_positions) == 1
        await session.run_once()
        assert len(session._manager.open_positions) == 1  # 未增加

    @pytest.mark.asyncio
    async def test_stop_loss_triggers_close(self):
        session = _make_session(
            opportunities=[_make_opportunity()],
            stop_loss_pct="0.001",
            slippage_bps="10",
            fee_rate="0.002",
        )
        await session.run_once()
        await session.run_once()
        closed = [
            p for p in session._manager.all_positions
            if p.exit_reason == ExitReason.STOP_LOSS
        ]
        assert len(closed) >= 1

    @pytest.mark.asyncio
    async def test_max_hold_time_triggers_close(self):
        session = _make_session(
            opportunities=[_make_opportunity()],
            max_hold_hours="0.001",
            max_positions=1,
        )
        await session.run_once()
        # 强制将开仓时间推到过去，触发超时
        for pos in session._manager.open_positions:
            pos.opened_at = datetime.now(UTC) - timedelta(hours=1)
        await session.run_once()
        timeout_closed = [
            p for p in session._manager.all_positions
            if p.exit_reason == ExitReason.MAX_HOLD_TIME
        ]
        assert len(timeout_closed) >= 1


# ---------------------------------------------------------------------------
# 资金费结算
# ---------------------------------------------------------------------------


class TestFundingSettlement:
    @pytest.mark.asyncio
    async def test_no_settlement_before_8_hours(self):
        session = _make_session(opportunities=[_make_opportunity()])
        session._last_funding_settled = datetime.now(UTC)  # 刚结算
        await session.run_once()
        total = sum(
            p.funding_received for p in session._manager.all_positions
        )
        assert total == Decimal("0")

    @pytest.mark.asyncio
    async def test_settlement_after_8_hours(self):
        session = _make_session(opportunities=[_make_opportunity(rate="0.0003")])
        await session.run_once()
        session._last_funding_settled = datetime.now(UTC) - timedelta(hours=9)
        await session.run_once()
        total = sum(
            p.funding_received for p in session._manager.all_positions
        )
        assert total > Decimal("0")

    @pytest.mark.asyncio
    async def test_negative_rate_skipped(self):
        session = _make_session(opportunities=[_make_opportunity(rate="-0.0003")])
        await session.run_once()
        session._last_funding_settled = datetime.now(UTC) - timedelta(hours=9)
        await session.run_once()
        total = sum(
            p.funding_received for p in session._manager.all_positions
        )
        assert total == Decimal("0")

    @pytest.mark.asyncio
    async def test_last_settled_updated_after_settlement(self):
        session = _make_session(opportunities=[_make_opportunity()])
        await session.run_once()
        cutoff = datetime.now(UTC) - timedelta(minutes=1)
        session._last_funding_settled = cutoff - timedelta(hours=8)
        await session.run_once()
        assert session._last_funding_settled > cutoff


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_empty_status_on_init(self):
        session = _make_session(opportunities=[])
        snap = session.status()
        assert isinstance(snap, StatusSnapshot)
        assert snap.open_positions == 0
        assert snap.total_trades == 0
        assert snap.net_pnl_usd == Decimal("0")

    @pytest.mark.asyncio
    async def test_status_reflects_open_position(self):
        session = _make_session(opportunities=[_make_opportunity()])
        await session.run_once()
        snap = session.status()
        assert snap.open_positions >= 1
        assert snap.total_trades >= 1

    @pytest.mark.asyncio
    async def test_unrealized_pnl_positive_on_price_increase(self):
        session = _make_session(
            opportunities=[_make_opportunity(spot_ask="60000")],
            fee_rate="0",
            slippage_bps="0",
        )
        await session.run_once()
        # 现货涨至 61000，永续不动（基差收窄）→ 多头收益 > 空头亏损
        higher_opp = _make_opportunity(spot_ask="61000", perp_bid="60010")
        snap = session.status(opportunities=[higher_opp])
        assert snap.unrealized_pnl_usd > Decimal("0")

    @pytest.mark.asyncio
    async def test_net_pnl_accounts_for_fees(self):
        session = _make_session(
            opportunities=[_make_opportunity()],
            fee_rate="0.001",
            slippage_bps="0",
        )
        await session.run_once()
        snap = session.status()
        assert snap.total_fees_usd > Decimal("0")
        expected = (
            sum(p.realized_pnl for p in session._manager.all_positions)
            + snap.total_funding_usd
            - snap.total_fees_usd
        )
        assert snap.net_pnl_usd == expected


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_sets_running_to_false(self):
        session = _make_session(opportunities=[])
        session._running = True
        await session.stop()
        assert session._running is False

    @pytest.mark.asyncio
    async def test_initial_running_is_false(self):
        session = _make_session(opportunities=[])
        assert session._running is False
