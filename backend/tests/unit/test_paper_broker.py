"""单元测试 — execution/paper_broker.py"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.exchanges.models import OrderBook, Side, Symbol
from app.execution.paper_broker import OrderRequest, OrderResult, PaperBroker

BTC = Symbol("BTC", "USDT")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _req(side: Side, price: str = "60000", size: str = "0.01") -> OrderRequest:
    return OrderRequest(
        symbol=BTC,
        side=side,
        size=Decimal(size),
        reference_price=Decimal(price),
    )


def _orderbook(bid: str = "59990", ask: str = "60000") -> OrderBook:
    return OrderBook(
        symbol=BTC,
        bids=[(Decimal(bid), Decimal("1"))],
        asks=[(Decimal(ask), Decimal("1"))],
        timestamp=1_700_000_000_000,
    )


# ---------------------------------------------------------------------------
# 滑点计算
# ---------------------------------------------------------------------------


class TestSlippage:
    @pytest.mark.asyncio
    async def test_buy_price_higher_than_reference(self):
        broker = PaperBroker(slippage_bps=Decimal("2"))
        result = await broker.execute(_req(Side.BUY, "60000"))
        assert result.avg_price > Decimal("60000")

    @pytest.mark.asyncio
    async def test_sell_price_lower_than_reference(self):
        broker = PaperBroker(slippage_bps=Decimal("2"))
        result = await broker.execute(_req(Side.SELL, "60000"))
        assert result.avg_price < Decimal("60000")

    @pytest.mark.asyncio
    async def test_buy_slippage_exact(self):
        broker = PaperBroker(slippage_bps=Decimal("2"))
        result = await broker.execute(_req(Side.BUY, "60000"))
        # 60000 × (1 + 2/10000) = 60012.00
        assert result.avg_price == Decimal("60012.00000000")

    @pytest.mark.asyncio
    async def test_sell_slippage_exact(self):
        broker = PaperBroker(slippage_bps=Decimal("2"))
        result = await broker.execute(_req(Side.SELL, "60000"))
        # 60000 × (1 − 2/10000) = 59988.00
        assert result.avg_price == Decimal("59988.00000000")

    @pytest.mark.asyncio
    async def test_zero_slippage_passes_reference_price(self):
        broker = PaperBroker(slippage_bps=Decimal("0"))
        result = await broker.execute(_req(Side.BUY, "60000"))
        assert result.avg_price == Decimal("60000.00000000")


# ---------------------------------------------------------------------------
# 手续费计算
# ---------------------------------------------------------------------------


class TestFees:
    @pytest.mark.asyncio
    async def test_fee_is_notional_times_rate(self):
        broker = PaperBroker(slippage_bps=Decimal("0"), fee_rate=Decimal("0.001"))
        result = await broker.execute(_req(Side.BUY, "60000", "0.01"))
        # notional = 60000 × 0.01 = 600; fee = 600 × 0.001 = 0.6
        assert result.fees.quantize(Decimal("0.0001")) == Decimal("0.6000")

    @pytest.mark.asyncio
    async def test_zero_fee_rate(self):
        broker = PaperBroker(slippage_bps=Decimal("0"), fee_rate=Decimal("0"))
        result = await broker.execute(_req(Side.BUY, "60000"))
        assert result.fees == Decimal("0")


# ---------------------------------------------------------------------------
# OrderResult 属性
# ---------------------------------------------------------------------------


class TestOrderResult:
    @pytest.mark.asyncio
    async def test_filled_is_true(self):
        result = await PaperBroker().execute(_req(Side.BUY))
        assert result.filled is True

    @pytest.mark.asyncio
    async def test_is_success_true(self):
        result = await PaperBroker().execute(_req(Side.BUY))
        assert result.is_success is True

    @pytest.mark.asyncio
    async def test_filled_size_equals_requested(self):
        result = await PaperBroker().execute(_req(Side.BUY, size="0.05"))
        assert result.filled_size == Decimal("0.05")

    @pytest.mark.asyncio
    async def test_notional_usd(self):
        broker = PaperBroker(slippage_bps=Decimal("0"))
        result = await broker.execute(_req(Side.BUY, "60000", "0.01"))
        assert result.notional_usd == Decimal("60000.00000000") * Decimal("0.01")

    @pytest.mark.asyncio
    async def test_filled_at_is_populated(self):
        result = await PaperBroker().execute(_req(Side.BUY))
        assert result.filled_at is not None


# ---------------------------------------------------------------------------
# execute_pair
# ---------------------------------------------------------------------------


class TestExecutePair:
    @pytest.mark.asyncio
    async def test_returns_two_results(self):
        broker = PaperBroker()
        spot, perp = await broker.execute_pair(
            _req(Side.BUY, "60000"),
            _req(Side.SELL, "60010"),
        )
        assert spot.filled is True
        assert perp.filled is True

    @pytest.mark.asyncio
    async def test_pair_adds_two_entries_to_history(self):
        broker = PaperBroker()
        await broker.execute_pair(_req(Side.BUY), _req(Side.SELL))
        assert len(broker.history) == 2

    @pytest.mark.asyncio
    async def test_spot_is_buy_perp_is_sell(self):
        broker = PaperBroker()
        spot, perp = await broker.execute_pair(
            _req(Side.BUY), _req(Side.SELL)
        )
        assert spot.request.side == Side.BUY
        assert perp.request.side == Side.SELL


# ---------------------------------------------------------------------------
# 历史记录
# ---------------------------------------------------------------------------


class TestHistory:
    @pytest.mark.asyncio
    async def test_history_accumulates_across_calls(self):
        broker = PaperBroker()
        await broker.execute(_req(Side.BUY))
        await broker.execute(_req(Side.SELL))
        assert len(broker.history) == 2

    def test_clear_history_empties_list(self):
        broker = PaperBroker()
        broker._history.append(object())  # type: ignore[arg-type]
        broker.clear_history()
        assert broker.history == []

    @pytest.mark.asyncio
    async def test_history_returns_copy_not_reference(self):
        broker = PaperBroker()
        await broker.execute(_req(Side.BUY))
        h = broker.history
        h.clear()
        assert len(broker.history) == 1  # 内部列表不受影响


# ---------------------------------------------------------------------------
# reference_price_from_book
# ---------------------------------------------------------------------------


class TestReferencePriceFromBook:
    def test_buy_uses_best_ask(self):
        ob = _orderbook(bid="59990", ask="60000")
        assert PaperBroker.reference_price_from_book(ob, Side.BUY) == Decimal("60000")

    def test_sell_uses_best_bid(self):
        ob = _orderbook(bid="59990", ask="60000")
        assert PaperBroker.reference_price_from_book(ob, Side.SELL) == Decimal("59990")

    def test_empty_asks_raises_value_error(self):
        ob = OrderBook(
            symbol=BTC,
            bids=[(Decimal("59990"), Decimal("1"))],
            asks=[],
            timestamp=0,
        )
        with pytest.raises(ValueError):
            PaperBroker.reference_price_from_book(ob, Side.BUY)

    def test_empty_bids_raises_value_error(self):
        ob = OrderBook(
            symbol=BTC,
            bids=[],
            asks=[(Decimal("60000"), Decimal("1"))],
            timestamp=0,
        )
        with pytest.raises(ValueError):
            PaperBroker.reference_price_from_book(ob, Side.SELL)
