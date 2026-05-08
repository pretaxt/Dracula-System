"""单元测试 — execution/live_broker.py"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exchanges.errors import InsufficientBalanceError, OrderRejectedError
from app.exchanges.models import (
    InstrumentType,
    Order,
    OrderStatus,
    OrderType,
    Side,
    Symbol,
)
from app.execution.live_broker import LiveBroker
from app.execution.paper_broker import OrderRequest

BTC = Symbol("BTC", "USDT")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _filled_order(
    instrument: InstrumentType,
    side: Side,
    size: str = "0.01",
    price: str = "60000",
) -> Order:
    return Order(
        order_id="OID",
        client_order_id="",
        symbol=BTC,
        instrument=instrument,
        side=side,
        order_type=OrderType.MARKET,
        size=Decimal(size),
        price=Decimal("0"),
        filled=Decimal(size),
        avg_fill_price=Decimal(price),
        status=OrderStatus.FILLED,
        timestamp=1_700_000_000_000,
        exchange="binance",
    )


def _make_adapter(place_order_return: Order | Exception | None = None) -> MagicMock:
    adapter = MagicMock()
    if isinstance(place_order_return, Exception):
        adapter.place_order = AsyncMock(side_effect=place_order_return)
    elif place_order_return is not None:
        adapter.place_order = AsyncMock(return_value=place_order_return)
    else:
        adapter.place_order = AsyncMock(
            return_value=_filled_order(InstrumentType.SPOT, Side.BUY)
        )

    usdm_client = MagicMock()
    usdm_client.set_margin_mode = AsyncMock(return_value=None)
    usdm_client.set_leverage = AsyncMock(return_value=None)
    usdm_client.amount_to_precision = MagicMock(side_effect=lambda sym, qty: f"{qty:.6f}")
    # 默认 USDM 钱包余额够（避免老测试受新 _ensure_perp_margin 干扰）
    usdm_client.fetch_balance = AsyncMock(
        return_value={"total": {"USDT": "1000"}, "free": {"USDT": "1000"}}
    )

    spot_client = MagicMock()
    spot_client.amount_to_precision = MagicMock(side_effect=lambda sym, qty: f"{qty:.6f}")
    spot_client.transfer = AsyncMock(return_value={"id": "tx_mock", "status": "ok"})

    adapter._clients = {
        InstrumentType.PERPETUAL: usdm_client,
        InstrumentType.SPOT: spot_client,
    }
    # 模拟 BinanceAdapter.top_up_perp_margin（默认成功）
    adapter.top_up_perp_margin = AsyncMock(return_value=None)
    return adapter


def _req(
    side: Side,
    instrument: InstrumentType = InstrumentType.SPOT,
    reduce_only: bool = False,
    size: str = "0.01",
    price: str = "60000",
) -> OrderRequest:
    return OrderRequest(
        symbol=BTC,
        side=side,
        size=Decimal(size),
        reference_price=Decimal(price),
        exchange="binance",
        reduce_only=reduce_only,
        instrument_type=instrument,
    )


# ---------------------------------------------------------------------------
# 构造函数 & 默认参数
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_default_fee_rate(self):
        broker = LiveBroker(adapter=_make_adapter())
        assert broker.fee_rate == Decimal("0.0002")

    def test_default_perp_leverage(self):
        broker = LiveBroker(adapter=_make_adapter())
        assert broker._perp_leverage == Decimal("5")

    def test_custom_fee_rate(self):
        broker = LiveBroker(adapter=_make_adapter(), fee_rate=Decimal("0.001"))
        assert broker.fee_rate == Decimal("0.001")

    def test_custom_perp_leverage(self):
        broker = LiveBroker(adapter=_make_adapter(), perp_leverage=Decimal("3"))
        assert broker._perp_leverage == Decimal("3")

    def test_leverage_initialized_starts_empty(self):
        broker = LiveBroker(adapter=_make_adapter())
        assert broker._leverage_initialized == set()


# ---------------------------------------------------------------------------
# execute() — 现货成功路径
# ---------------------------------------------------------------------------


class TestExecuteSpot:
    @pytest.mark.asyncio
    async def test_spot_buy_returns_filled_result(self):
        adapter = _make_adapter(_filled_order(InstrumentType.SPOT, Side.BUY))
        broker = LiveBroker(adapter=adapter)
        result = await broker.execute(_req(Side.BUY, InstrumentType.SPOT))
        assert result.filled is True
        assert result.avg_price == Decimal("60000")
        assert result.filled_size == Decimal("0.01")

    @pytest.mark.asyncio
    async def test_spot_buy_calculates_fees(self):
        adapter = _make_adapter(_filled_order(InstrumentType.SPOT, Side.BUY))
        broker = LiveBroker(adapter=adapter, fee_rate=Decimal("0.001"))
        result = await broker.execute(_req(Side.BUY, InstrumentType.SPOT))
        # 60000 × 0.01 × 0.001 = 0.6
        assert result.fees == Decimal("0.6")

    @pytest.mark.asyncio
    async def test_spot_does_not_set_leverage(self):
        adapter = _make_adapter(_filled_order(InstrumentType.SPOT, Side.BUY))
        broker = LiveBroker(adapter=adapter)
        await broker.execute(_req(Side.BUY, InstrumentType.SPOT))
        adapter._clients[InstrumentType.PERPETUAL].set_leverage.assert_not_called()

    @pytest.mark.asyncio
    async def test_filled_at_populated(self):
        adapter = _make_adapter(_filled_order(InstrumentType.SPOT, Side.BUY))
        broker = LiveBroker(adapter=adapter)
        result = await broker.execute(_req(Side.BUY, InstrumentType.SPOT))
        assert result.filled_at is not None


# ---------------------------------------------------------------------------
# execute() — 永续 leverage 自动设置
# ---------------------------------------------------------------------------


class TestPerpLeverageAutoSet:
    @pytest.mark.asyncio
    async def test_perp_open_calls_set_leverage(self):
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.SELL))
        broker = LiveBroker(adapter=adapter, perp_leverage=Decimal("5"))
        await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL))
        adapter._clients[InstrumentType.PERPETUAL].set_leverage.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_leverage_passes_correct_value(self):
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.SELL))
        broker = LiveBroker(adapter=adapter, perp_leverage=Decimal("3"))
        await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL))
        # 第一个位置参数必须是 int(3)
        call = adapter._clients[InstrumentType.PERPETUAL].set_leverage.await_args
        assert call.args[0] == 3

    @pytest.mark.asyncio
    async def test_set_leverage_only_called_once_per_symbol(self):
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.SELL))
        broker = LiveBroker(adapter=adapter)
        await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL))
        await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL))
        assert adapter._clients[InstrumentType.PERPETUAL].set_leverage.await_count == 1

    @pytest.mark.asyncio
    async def test_reduce_only_does_not_set_leverage(self):
        """平仓单不应再设杠杆（也避免 set_leverage 在仓位存在时报错）。"""
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.BUY))
        broker = LiveBroker(adapter=adapter)
        await broker.execute(_req(Side.BUY, InstrumentType.PERPETUAL, reduce_only=True))
        adapter._clients[InstrumentType.PERPETUAL].set_leverage.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_leverage_failure_does_not_block_order(self):
        """杠杆设置失败时仅记 warning，不影响后续下单。"""
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.SELL))
        adapter._clients[InstrumentType.PERPETUAL].set_leverage = AsyncMock(
            side_effect=RuntimeError("rate limit")
        )
        broker = LiveBroker(adapter=adapter)
        # 不抛异常，订单照下
        result = await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL))
        assert result.filled is True


# ---------------------------------------------------------------------------
# execute() — 永续 reduce_only "已强平" 处理
# ---------------------------------------------------------------------------


class TestReduceOnlyAlreadyClosed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "msg",
        [
            "Position is not exist",
            "ReduceOnly Order is rejected.",
            "Position side does not match",
            "Order does not exist",
        ],
    )
    async def test_already_closed_phrases_return_skip_flag(self, msg):
        adapter = _make_adapter(OrderRejectedError(msg))
        broker = LiveBroker(adapter=adapter)
        result = await broker.execute(
            _req(Side.BUY, InstrumentType.PERPETUAL, reduce_only=True)
        )
        assert result.leg_already_closed is True
        assert result.filled is False
        assert result.filled_size == Decimal("0")

    @pytest.mark.asyncio
    async def test_unrelated_error_on_reduce_only_reraises(self):
        """非"已关"消息（如 nonce 错误）不应被吞掉。"""
        adapter = _make_adapter(OrderRejectedError("Invalid signature"))
        broker = LiveBroker(adapter=adapter)
        with pytest.raises(OrderRejectedError):
            await broker.execute(
                _req(Side.BUY, InstrumentType.PERPETUAL, reduce_only=True)
            )

    @pytest.mark.asyncio
    async def test_already_closed_phrase_on_open_order_reraises(self):
        """开仓单（reduce_only=False）即便消息里含 "does not exist" 也必须抛出。"""
        adapter = _make_adapter(OrderRejectedError("Symbol does not exist"))
        broker = LiveBroker(adapter=adapter)
        with pytest.raises(OrderRejectedError):
            await broker.execute(
                _req(Side.SELL, InstrumentType.PERPETUAL, reduce_only=False)
            )

    @pytest.mark.asyncio
    async def test_already_closed_phrase_on_spot_reduce_only_reraises(self):
        """现货 reduce_only 的"已关"白名单仅永续生效。"""
        adapter = _make_adapter(OrderRejectedError("Position is not exist"))
        broker = LiveBroker(adapter=adapter)
        with pytest.raises(OrderRejectedError):
            await broker.execute(
                _req(Side.SELL, InstrumentType.SPOT, reduce_only=True)
            )

    @pytest.mark.asyncio
    async def test_insufficient_balance_on_reduce_only_perp_with_skip_phrase(self):
        adapter = _make_adapter(InsufficientBalanceError("position is not exist"))
        broker = LiveBroker(adapter=adapter)
        result = await broker.execute(
            _req(Side.BUY, InstrumentType.PERPETUAL, reduce_only=True)
        )
        assert result.leg_already_closed is True


# ---------------------------------------------------------------------------
# execute_pair() — 双腿编排
# ---------------------------------------------------------------------------


class TestExecutePair:
    @pytest.mark.asyncio
    async def test_pair_happy_path_returns_two_results(self):
        adapter = _make_adapter()
        responses = [
            _filled_order(InstrumentType.SPOT, Side.BUY),
            _filled_order(InstrumentType.PERPETUAL, Side.SELL, price="60010"),
        ]
        adapter.place_order = AsyncMock(side_effect=responses)

        broker = LiveBroker(adapter=adapter)
        spot, perp = await broker.execute_pair(
            _req(Side.BUY, InstrumentType.SPOT),
            _req(Side.SELL, InstrumentType.PERPETUAL),
        )
        assert spot.filled is True
        assert perp.filled is True

    @pytest.mark.asyncio
    async def test_pair_perp_failure_unwinds_spot(self):
        """perp 开仓失败时，对已成交 spot 必须执行反向 SELL 回卷。"""
        adapter = _make_adapter()
        responses = [
            _filled_order(InstrumentType.SPOT, Side.BUY),  # 现货成功
            OrderRejectedError("perp open failed"),         # perp 失败
            _filled_order(InstrumentType.SPOT, Side.SELL),  # 回卷成功
        ]
        adapter.place_order = AsyncMock(side_effect=responses)

        broker = LiveBroker(adapter=adapter)
        with pytest.raises(RuntimeError, match="perp open failed"):
            await broker.execute_pair(
                _req(Side.BUY, InstrumentType.SPOT),
                _req(Side.SELL, InstrumentType.PERPETUAL),
            )
        # 调用 3 次：现货 BUY → perp SELL（失败）→ 现货 SELL（回卷）
        assert adapter.place_order.await_count == 3
        last_call = adapter.place_order.await_args_list[-1]
        assert last_call.kwargs["side"] == Side.SELL
        assert last_call.kwargs["instrument"] == InstrumentType.SPOT

    @pytest.mark.asyncio
    async def test_pair_perp_failure_with_zero_spot_skips_unwind(self):
        """spot 完全没成交时，回卷应直接跳过（不发空 SELL）。"""
        adapter = _make_adapter()
        empty_spot = _filled_order(InstrumentType.SPOT, Side.BUY)
        empty_spot.filled = Decimal("0")
        empty_spot.avg_fill_price = Decimal("0")

        responses = [
            empty_spot,
            OrderRejectedError("perp failed"),
        ]
        adapter.place_order = AsyncMock(side_effect=responses)

        broker = LiveBroker(adapter=adapter)
        with pytest.raises(RuntimeError):
            await broker.execute_pair(
                _req(Side.BUY, InstrumentType.SPOT),
                _req(Side.SELL, InstrumentType.PERPETUAL),
            )
        # 仅 2 次：spot + perp，无回卷
        assert adapter.place_order.await_count == 2

    @pytest.mark.asyncio
    async def test_pair_perp_failure_unwind_failure_still_raises_original(self):
        """回卷本身也失败时，仍必须抛出最初的 perp 失败错误。"""
        adapter = _make_adapter()
        responses = [
            _filled_order(InstrumentType.SPOT, Side.BUY),
            OrderRejectedError("perp open failed"),
            OrderRejectedError("unwind also failed"),
        ]
        adapter.place_order = AsyncMock(side_effect=responses)

        broker = LiveBroker(adapter=adapter)
        with pytest.raises(RuntimeError, match="perp open failed"):
            await broker.execute_pair(
                _req(Side.BUY, InstrumentType.SPOT),
                _req(Side.SELL, InstrumentType.PERPETUAL),
            )


# ---------------------------------------------------------------------------
# _round_qty
# ---------------------------------------------------------------------------


class TestEnsurePerpMargin:
    @pytest.mark.asyncio
    async def test_balance_sufficient_no_transfer(self):
        """USDM 钱包足够时不该划转。"""
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.SELL))
        # 需要 margin = 0.01 × 60000 / 5 = 120；target = 144；balance 1000 够
        broker = LiveBroker(adapter=adapter, perp_leverage=Decimal("5"))
        await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL,
                                  size="0.01", price="60000"))
        adapter._clients[InstrumentType.SPOT].transfer.assert_not_called()

    @pytest.mark.asyncio
    async def test_balance_zero_triggers_transfer(self):
        """USDM 钱包空时划转 = required×1.2 + 1 = 13。"""
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.SELL))
        adapter._clients[InstrumentType.PERPETUAL].fetch_balance = AsyncMock(
            return_value={"total": {"USDT": "0"}, "free": {"USDT": "0"}}
        )
        broker = LiveBroker(adapter=adapter, perp_leverage=Decimal("5"))
        # size=0.001 × price=50000 = $50 notional, margin = $10, target = $12, transfer = $13
        await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL,
                                  size="0.001", price="50000"))
        adapter.top_up_perp_margin.assert_awaited_once()
        amount = adapter.top_up_perp_margin.await_args.args[0]
        assert abs(float(amount) - 13.0) < 0.01  # required×1.2 + 1 buffer

    @pytest.mark.asyncio
    async def test_partial_balance_transfers_only_shortfall(self):
        """USDM 钱包有 5 USDT，需要 12，划转 12-5+1 = 8。"""
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.SELL))
        adapter._clients[InstrumentType.PERPETUAL].fetch_balance = AsyncMock(
            return_value={"total": {"USDT": "5"}, "free": {"USDT": "5"}}
        )
        broker = LiveBroker(adapter=adapter, perp_leverage=Decimal("5"))
        await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL,
                                  size="0.001", price="50000"))
        amount = adapter.top_up_perp_margin.await_args.args[0]
        assert abs(float(amount) - 8.0) < 0.01

    @pytest.mark.asyncio
    async def test_reduce_only_does_not_transfer(self):
        """平仓单不该触发划转（保证金已锁，平仓回流，不需要补充）。"""
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.BUY))
        # 故意把余额设 0，看会不会触发
        adapter._clients[InstrumentType.PERPETUAL].fetch_balance = AsyncMock(
            return_value={"total": {"USDT": "0"}, "free": {"USDT": "0"}}
        )
        broker = LiveBroker(adapter=adapter)
        await broker.execute(_req(Side.BUY, InstrumentType.PERPETUAL,
                                  reduce_only=True, size="0.001", price="50000"))
        adapter._clients[InstrumentType.SPOT].transfer.assert_not_called()

    @pytest.mark.asyncio
    async def test_spot_order_does_not_transfer(self):
        """现货订单不该触发 USDM 划转检查。"""
        adapter = _make_adapter(_filled_order(InstrumentType.SPOT, Side.BUY))
        broker = LiveBroker(adapter=adapter)
        await broker.execute(_req(Side.BUY, InstrumentType.SPOT))
        adapter._clients[InstrumentType.SPOT].transfer.assert_not_called()

    @pytest.mark.asyncio
    async def test_transfer_failure_does_not_block_order(self):
        """transfer API 抛错时仅记 warning，不影响后续 place_order。"""
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.SELL))
        adapter._clients[InstrumentType.PERPETUAL].fetch_balance = AsyncMock(
            return_value={"total": {"USDT": "0"}, "free": {"USDT": "0"}}
        )
        adapter.top_up_perp_margin = AsyncMock(side_effect=RuntimeError("rate limit"))
        broker = LiveBroker(adapter=adapter)
        # 即便 transfer 失败，order 照下（让交易所自己反馈是否真不够）
        result = await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL,
                                            size="0.001", price="50000"))
        assert result.filled is True

    @pytest.mark.asyncio
    async def test_fetch_balance_failure_does_not_block_order(self):
        adapter = _make_adapter(_filled_order(InstrumentType.PERPETUAL, Side.SELL))
        adapter._clients[InstrumentType.PERPETUAL].fetch_balance = AsyncMock(
            side_effect=RuntimeError("network error")
        )
        broker = LiveBroker(adapter=adapter)
        result = await broker.execute(_req(Side.SELL, InstrumentType.PERPETUAL,
                                            size="0.001", price="50000"))
        assert result.filled is True


class TestRoundQty:
    def test_uses_adapter_precision(self):
        adapter = _make_adapter()
        adapter._clients[InstrumentType.SPOT].amount_to_precision = MagicMock(
            return_value="0.01234"
        )
        broker = LiveBroker(adapter=adapter)
        rounded = broker._round_qty(BTC, InstrumentType.SPOT, Decimal("0.0123456789"))
        assert rounded == Decimal("0.01234")

    def test_falls_back_to_original_on_error(self):
        adapter = _make_adapter()
        adapter._clients[InstrumentType.SPOT].amount_to_precision = MagicMock(
            side_effect=KeyError("symbol not in markets")
        )
        broker = LiveBroker(adapter=adapter)
        original = Decimal("0.0123456789")
        rounded = broker._round_qty(BTC, InstrumentType.SPOT, original)
        assert rounded == original
