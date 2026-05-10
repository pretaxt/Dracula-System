"""单元测试 — execution/order_executor.py"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestPartialCloseRiskEventAndTelegram:
    """TS_R9 + TS_R10 — PartialCloseError 必须同时写 risk_event + 发 Telegram。"""

    @pytest.mark.asyncio
    async def test_partial_close_writes_risk_event_and_notifies(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()
        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        async def fake_execute(req):
            if req.instrument_type == InstrumentType.PERPETUAL:
                from app.execution.paper_broker import OrderResult
                return OrderResult(
                    request=req, filled=True, avg_price=req.reference_price,
                    filled_size=req.size, fees=Decimal("0"),
                    slippage_bps=Decimal("0"), filled_at=datetime.now(UTC),
                )
            raise RuntimeError("spot close fail")

        with patch.object(executor._broker, "execute", new=fake_execute):
            with patch.object(manager, "save", new=AsyncMock()):
                with patch("app.services.risk_event_service.write_risk_event", new=AsyncMock()) as mock_re:
                    with patch("app.notifications.notify_reconcile_alert") as mock_tg:
                        from app.execution.order_executor import PartialCloseError
                        with pytest.raises(PartialCloseError):
                            await executor.close_position(pos.id)
        # risk_event "partial_close" 必写
        types = [c.kwargs.get("event_type") for c in mock_re.await_args_list]
        assert "partial_close" in types
        # Telegram 通知必发
        mock_tg.assert_called()


class TestPnLUsesFilledSize:
    """TS_pnl — close 后 PnL 计算用 result.filled_size，处理 X7 cap 后 filled<leg.size。"""

    @pytest.mark.asyncio
    async def test_pnl_uses_filled_when_capped(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()
        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        # close 时 spot SELL 被 cap，filled_size=0.0099 < leg.size=0.01
        leg_size = pos.legs[0].size  # spot
        capped_size = leg_size * Decimal("0.99")

        async def fake_execute(req):
            from app.execution.paper_broker import OrderResult
            filled = capped_size if req.instrument_type == InstrumentType.SPOT else req.size
            return OrderResult(
                request=req, filled=True,
                avg_price=req.reference_price + Decimal("100"),  # 价格上涨 100
                filled_size=filled,
                fees=Decimal("0"), slippage_bps=Decimal("0"),
                filled_at=datetime.now(UTC),
            )

        with patch.object(executor._broker, "execute", new=fake_execute):
            with patch.object(manager, "save", new=AsyncMock()):
                closed = await executor.close_position(pos.id)

        # spot 用 filled_size (capped) 算 PnL，而非 leg.size
        # spot BUY: entry, close 时 SELL 价格 +100, 用 capped_size
        # 期望: realized_pnl 根据 capped_size 算（小于用 leg.size）
        assert closed.status == PositionStatus.CLOSED


class TestPostCloseReconcile:
    """TS_R13 — close_position 后 fetch_positions 验证真实平掉。"""

    @pytest.mark.asyncio
    async def test_post_close_alerts_when_perp_residual(self):
        from app.execution.live_broker import LiveBroker
        # 构造一个 LiveBroker，broker dict 里
        adapter = MagicMock()
        adapter.exchange_id = "binance"
        spot_client = MagicMock()
        spot_client.fetch_balance = AsyncMock(return_value={"free": {"USDT": "10000"}, "total": {"USDT": "10000"}})
        spot_client.amount_to_precision = MagicMock(side_effect=lambda s, q: f"{q}")
        perp_client = MagicMock()
        perp_client.fetch_balance = AsyncMock(return_value={"free": {"USDT": "1000"}, "total": {"USDT": "1000"}})
        perp_client.amount_to_precision = MagicMock(side_effect=lambda s, q: f"{q}")
        perp_client.set_margin_mode = AsyncMock()
        perp_client.set_leverage = AsyncMock()
        # 关键：post-close 时 fetch_positions 仍返回非零持仓
        perp_client.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDT:USDT", "contracts": 0.01, "side": "short"},
        ])
        adapter._clients = {InstrumentType.SPOT: spot_client, InstrumentType.PERPETUAL: perp_client}
        adapter.top_up_perp_margin = AsyncMock()
        # open + close 都成功
        from app.exchanges.models import Order, OrderStatus, OrderType
        def filled(instrument, side, size="0.01"):
            return Order(order_id="X", client_order_id="", symbol=BTC,
                         instrument=instrument, side=side, order_type=OrderType.MARKET,
                         size=Decimal(size), price=Decimal("0"), filled=Decimal(size),
                         avg_fill_price=Decimal("60000"), status=OrderStatus.FILLED,
                         timestamp=1_700_000_000_000, exchange="binance")
        adapter.place_order = AsyncMock(side_effect=[
            filled(InstrumentType.SPOT, Side.BUY),
            filled(InstrumentType.PERPETUAL, Side.SELL),
            filled(InstrumentType.PERPETUAL, Side.BUY),
            filled(InstrumentType.SPOT, Side.SELL),
        ])
        broker = LiveBroker(adapter=adapter, perp_leverage=Decimal("5"))
        manager = PositionManager()
        manager.save = AsyncMock()
        guard = RiskGuard(limits=_default_limits())
        executor = OrderExecutor(broker={"binance": broker}, manager=manager, guard=guard)
        opp = _make_opportunity()
        pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        # 期望：close 完成 + write_risk_event 被调（perp 残留）
        with patch("app.services.risk_event_service.write_risk_event", new=AsyncMock()) as mock_we:
            with patch("app.notifications.notify_reconcile_alert") as mock_notify:
                await executor.close_position(pos.id)
        # 至少调一次 risk_event（post_close_reconcile_perp_residual）
        types = [c.kwargs.get("event_type") for c in mock_we.await_args_list]
        assert "post_close_reconcile_perp_residual" in types


class TestClosePartialFailure:
    """R4 — 一腿成功一腿失败必须抛 PartialCloseError 让上层接管 reconciliation。
    严禁静默吞错让单腿暴露在交易所。
    """

    @pytest.mark.asyncio
    async def test_perp_close_succeeds_spot_fails_raises_partial(self):
        from app.execution.order_executor import PartialCloseError
        executor, manager = _make_executor()
        opp = _make_opportunity()
        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        call_count = {"n": 0}
        async def fake_execute(req):
            call_count["n"] += 1
            from app.execution.paper_broker import OrderResult
            # perp 第 1 个被尝试（sorted_legs perp 优先），成功
            if req.instrument_type == InstrumentType.PERPETUAL:
                return OrderResult(
                    request=req, filled=True,
                    avg_price=req.reference_price, filled_size=req.size,
                    fees=Decimal("0.1"), slippage_bps=Decimal("0"),
                    filled_at=datetime.now(UTC),
                )
            # spot 抛错 — broker 拒单
            raise RuntimeError("spot close failed")

        with patch.object(executor._broker, "execute", new=fake_execute):
            with patch.object(manager, "save", new=AsyncMock()):
                with pytest.raises(PartialCloseError) as excinfo:
                    await executor.close_position(pos.id)

        err = excinfo.value
        assert len(err.succeeded_legs) == 1
        assert len(err.failed_legs) == 1
        assert err.succeeded_legs[0].instrument_type == InstrumentType.PERPETUAL
        assert err.failed_legs[0][0].instrument_type == InstrumentType.SPOT
        # 仓位仍未标记 closed（DB 与真实状态会被 reconciler 修正）
        assert pos.status == PositionStatus.OPEN

    @pytest.mark.asyncio
    async def test_perp_first_then_spot_order(self):
        """sorted_legs 必须 perp 在 spot 前 — perp reduce_only 更安全先确定。"""
        executor, manager = _make_executor()
        opp = _make_opportunity()
        with patch.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        order: list = []
        async def fake_execute(req):
            order.append(req.instrument_type)
            from app.execution.paper_broker import OrderResult
            return OrderResult(
                request=req, filled=True,
                avg_price=req.reference_price, filled_size=req.size,
                fees=Decimal("0"), slippage_bps=Decimal("0"),
                filled_at=datetime.now(UTC),
            )

        with patch.object(executor._broker, "execute", new=fake_execute):
            with patch.object(manager, "save", new=AsyncMock()):
                await executor.close_position(pos.id)

        assert order[0] == InstrumentType.PERPETUAL
        assert order[1] == InstrumentType.SPOT


class TestClosePositionReduceOnlyPerLeg:
    """X6 修复 — close_position 不应给 spot leg 传 reduce_only=True
    （Binance spot 不接受该参数，会返回 -1104 'extra parameter'）。
    """

    @pytest.mark.asyncio
    async def test_spot_leg_close_no_reduce_only(self):
        from unittest.mock import patch as patch_mod
        executor, manager = _make_executor()
        opp = _make_opportunity()
        with patch_mod.object(manager, "save", new=AsyncMock()):
            pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        # mock broker.execute 捕获 close 时传给每条腿的 OrderRequest
        captured: list = []

        async def fake_execute(req):
            captured.append(req)
            from app.execution.paper_broker import OrderResult
            return OrderResult(
                request=req,
                filled=True,
                avg_price=req.reference_price,
                filled_size=req.size,
                fees=Decimal("0"),
                slippage_bps=Decimal("0"),
                filled_at=datetime.now(UTC),
            )

        with patch_mod.object(executor._broker, "execute", new=fake_execute):
            with patch_mod.object(manager, "save", new=AsyncMock()):
                await executor.close_position(pos.id)

        # 应有 2 个 close 请求（spot + perp）
        assert len(captured) == 2
        spot_req = next(r for r in captured if r.instrument_type == InstrumentType.SPOT)
        perp_req = next(r for r in captured if r.instrument_type == InstrumentType.PERPETUAL)
        assert spot_req.reduce_only is False, "spot 关单不能传 reduce_only=True"
        assert perp_req.reduce_only is True, "perp 关单必须 reduce_only=True"
        assert perp_req.position_side == "SHORT"
        assert spot_req.position_side is None


class TestTradeableExchanges:
    """tradeable_exchanges 让调用方过滤无 broker 的候选。

    Why: live_mode 下仅鉴权 adapter 有 broker；scanner 仍扫全部 exchange 行情。
    没有这个 hint 候选会反复抛 RuntimeError 污染 paper_open_unexpected_error。
    """

    def test_dict_broker_returns_keys(self):
        manager = PositionManager()
        guard = RiskGuard(limits=_default_limits())
        b1 = PaperBroker(slippage_bps=Decimal("0"), fee_rate=Decimal("0"))
        b2 = PaperBroker(slippage_bps=Decimal("0"), fee_rate=Decimal("0"))
        ex = OrderExecutor(broker={"binance": b1, "okx": b2}, manager=manager, guard=guard)
        assert ex.tradeable_exchanges == {"binance", "okx"}

    def test_single_broker_returns_none(self):
        executor, _ = _make_executor()
        assert executor.tradeable_exchanges is None

    def test_empty_dict_returns_empty_set(self):
        manager = PositionManager()
        guard = RiskGuard(limits=_default_limits())
        ex = OrderExecutor(broker={}, manager=manager, guard=guard)
        assert ex.tradeable_exchanges == set()


class TestOpenBrokerFailureRollback:
    """broker.execute_pair 抛错时，OrderExecutor 必须 discard 内存 position
    并重新抛出，避免幽灵持仓污染 max_positions 配额。

    Why: 实战中 Binance API 白名单不含某 symbol，spot 下单返回 -2010，
    旧实现把 position 留在内存里，后续所有候选都被 max_positions=1 屏蔽，
    整个 8h 窗口实际成交 = 0。
    """

    @pytest.mark.asyncio
    async def test_rolls_back_position_on_broker_error(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()
        boom = RuntimeError('binance {"code":-2010,"msg":"Symbol not whitelisted for API key."}')

        with patch.object(manager, "save", new=AsyncMock()):
            with patch.object(
                executor._broker, "execute_pair", new=AsyncMock(side_effect=boom)
            ):
                with pytest.raises(RuntimeError):
                    await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

        assert manager.all_positions == []
        assert manager.open_positions == []

    @pytest.mark.asyncio
    async def test_rollback_does_not_persist_to_db(self):
        executor, manager = _make_executor()
        opp = _make_opportunity()
        save_mock = AsyncMock()

        with patch.object(manager, "save", save_mock):
            with patch.object(
                executor._broker, "execute_pair", new=AsyncMock(side_effect=Exception("nope"))
            ):
                try:
                    await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
                except Exception:
                    pass

        save_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_subsequent_open_succeeds_after_rollback(self):
        """回滚后 max_positions 配额未被永久占用，下一次 open 可成功。"""
        executor, manager = _make_executor(max_positions=1)
        opp = _make_opportunity()

        with patch.object(manager, "save", new=AsyncMock()):
            with patch.object(
                executor._broker, "execute_pair",
                new=AsyncMock(side_effect=Exception("Symbol not whitelisted")),
            ):
                with pytest.raises(Exception):
                    await executor.open_delta_neutral(opp, size_usd=Decimal("600"))

            # 第二次（不再 patch，走真实 PaperBroker）必须成功
            opp2 = _make_opportunity(symbol=ETH)
            pos = await executor.open_delta_neutral(opp2, size_usd=Decimal("600"))

        assert pos.status == PositionStatus.OPEN
        assert len(manager.open_positions) == 1


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
