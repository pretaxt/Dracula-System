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
    next_funding_time: int | None = None,
) -> FundingRateOpportunity:
    """构造一个机会；默认 next_funding_time 落在 pre-funding 15min 窗口内（now+10min）。"""
    if next_funding_time is None:
        next_funding_time = int(
            (datetime.now(UTC) + timedelta(minutes=10)).timestamp() * 1000
        )
    funding = FundingRate(
        symbol=BTC, exchange="binance",
        rate=Decimal(rate),
        next_funding_time=next_funding_time,
        funding_interval_hours=8,
    )
    spot_ob = OrderBook(
        symbol=BTC,
        bids=[(Decimal(spot_ask) - Decimal("10"), Decimal("10"))],
        asks=[(Decimal(spot_ask), Decimal("10"))],
        timestamp=next_funding_time,
    )
    perp_ob = OrderBook(
        symbol=BTC,
        bids=[(Decimal(perp_bid), Decimal("10"))],
        asks=[(Decimal(perp_bid) + Decimal("10"), Decimal("10"))],
        timestamp=next_funding_time,
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
    # 默认资金费率仍为正、价格不变 → 不触发 funding_reversal / perp_margin 退出
    mock_scanner.current_rate = AsyncMock(return_value=None)
    mock_scanner.current_perp_price = AsyncMock(return_value=None)

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


# ---------------------------------------------------------------------------
# _maybe_close_perp_margin_risk — 永续单腿保证金亏损保护
# ---------------------------------------------------------------------------
#
# 5x 杠杆 + 80% 阈值的清算缓冲设计：
#   初始保证金 = notional / leverage = 600 / 5 = 120 USD
#   loss_pct >= 80% → unrealized_loss >= 96 USD
#   SHORT entry=60000, size=0.01 → unrealized_loss = (current-entry) * size
#   触发临界 current = entry + 96/0.01 = entry + 9600 = entry × 1.16 (+16%)
#   Binance 5x SHORT 强平在 +19.5%，所以 +16% 是策略主动同关，避免被交易所拆腿
#
# ---------------------------------------------------------------------------


from app.exchanges.models import InstrumentType, Side  # noqa: E402  (test-only)
from app.risk.models import Position, PositionLeg, PositionStatus  # noqa: E402


def _make_perp_margin_session(
    threshold_pct: str = "80",
    current_perp_price: str | None = "60000",
):
    """构造一个 session，scanner.current_perp_price 可控、executor.close_position 可断言。"""
    session = _make_session(opportunities=[])
    session._perp_margin_loss_threshold = Decimal(threshold_pct)
    if current_perp_price is None:
        session._scanner.current_perp_price = AsyncMock(return_value=None)
    else:
        session._scanner.current_perp_price = AsyncMock(
            return_value=Decimal(current_perp_price)
        )
    session._executor.close_position = AsyncMock(return_value=None)
    return session


def _delta_neutral_position(
    entry_price: str = "60000",
    size: str = "0.01",
    leverage: str = "5",
) -> Position:
    """构造已开仓的 Delta 中性 Position（spot BUY + perp SELL）。"""
    pos = Position(
        strategy_instance="test",
        symbol=BTC,
        notional_usd=Decimal(entry_price) * Decimal(size),
        status=PositionStatus.OPEN,
    )
    pos.legs.append(PositionLeg(
        exchange="binance",
        symbol=BTC,
        instrument_type=InstrumentType.SPOT,
        side=Side.BUY,
        size=Decimal(size),
        entry_price=Decimal(entry_price),
    ))
    pos.legs.append(PositionLeg(
        exchange="binance",
        symbol=BTC,
        instrument_type=InstrumentType.PERPETUAL,
        side=Side.SELL,
        size=Decimal(size),
        entry_price=Decimal(entry_price),
        leverage=Decimal(leverage),
    ))
    return pos


class TestPerpMarginRisk:
    @pytest.mark.asyncio
    async def test_loss_above_threshold_triggers_close(self):
        """+16% 价格 → 80% 保证金亏 → 触发同关。"""
        session = _make_perp_margin_session(threshold_pct="80",
                                            current_perp_price="69600")
        pos = _delta_neutral_position()
        triggered = await session._maybe_close_perp_margin_risk(pos, "binance")
        assert triggered is True
        session._executor.close_position.assert_awaited_once()
        kwargs = session._executor.close_position.await_args.kwargs
        assert kwargs.get("reason") == ExitReason.PERP_LIQ_RISK

    @pytest.mark.asyncio
    async def test_loss_below_threshold_does_not_trigger(self):
        """+5% 价格 → 25% 保证金亏 → 不触发。"""
        session = _make_perp_margin_session(threshold_pct="80",
                                            current_perp_price="63000")
        pos = _delta_neutral_position()
        triggered = await session._maybe_close_perp_margin_risk(pos, "binance")
        assert triggered is False
        session._executor.close_position.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_price_below_entry_no_loss_no_trigger(self):
        """价格下跌 → SHORT 盈利 → 永远不触发。"""
        session = _make_perp_margin_session(threshold_pct="80",
                                            current_perp_price="55000")
        pos = _delta_neutral_position()
        triggered = await session._maybe_close_perp_margin_risk(pos, "binance")
        assert triggered is False
        session._executor.close_position.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_current_price_none_does_not_trigger(self):
        """current_perp_price 拉取失败 → 保守不动（避免凭旧数据误关仓）。"""
        session = _make_perp_margin_session(threshold_pct="80", current_perp_price=None)
        pos = _delta_neutral_position()
        triggered = await session._maybe_close_perp_margin_risk(pos, "binance")
        assert triggered is False
        session._executor.close_position.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_perp_leg_does_not_trigger(self):
        """仓位只有现货腿 → 直接返回 False。"""
        session = _make_perp_margin_session()
        pos = _delta_neutral_position()
        pos.legs = [pos.legs[0]]
        triggered = await session._maybe_close_perp_margin_risk(pos, "binance")
        assert triggered is False

    @pytest.mark.asyncio
    async def test_threshold_at_exact_boundary_triggers(self):
        """精确 80% 边界（+16%）刚触发——边界含等号。"""
        session = _make_perp_margin_session(threshold_pct="80",
                                            current_perp_price="69600")
        pos = _delta_neutral_position(leverage="5")
        triggered = await session._maybe_close_perp_margin_risk(pos, "binance")
        assert triggered is True

    @pytest.mark.asyncio
    async def test_higher_leverage_triggers_at_smaller_price_move(self):
        """10x 杠杆 + 80% 阈值 → +8% 触发（10x 时 margin 减半）。"""
        # initial_margin = 600/10 = 60. 80% × 60 = 48. (current-entry)×0.01 = 48 → +4800 → +8%
        session = _make_perp_margin_session(threshold_pct="80",
                                            current_perp_price="64800")
        pos = _delta_neutral_position(leverage="10")
        triggered = await session._maybe_close_perp_margin_risk(pos, "binance")
        assert triggered is True
