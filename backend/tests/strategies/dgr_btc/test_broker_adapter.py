"""Tests for DgrBtcBrokerAdapter (mock binance adapter)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.strategies.dgr_btc.broker_adapter import (
    DgrBtcBrokerAdapter,
    DgrBtcBrokerConfig,
)
from app.strategies.dgr_btc.maker_reprice import RejectError
from app.strategies.dgr_btc.types import MarketType, Side


def _mock_adapter(**overrides):
    """Minimal mock binance adapter."""
    a = MagicMock()
    a.cancel_order = AsyncMock()
    a.fetch_open_orders = AsyncMock(return_value=[])
    a.fetch_balance = AsyncMock(return_value={"BTC": {"free": 0.1}})
    a.fetch_positions = AsyncMock(return_value=[])
    a.fetch_ticker = AsyncMock(return_value={"last": 76800.0})
    a.set_leverage = AsyncMock()
    a.set_margin_mode = AsyncMock()
    for k, v in overrides.items():
        setattr(a, k, v)
    return a


# ============================================================
# DRY_RUN 模式
# ============================================================

class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_returns_mock_trade(self) -> None:
        adapter = _mock_adapter()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=True)
        trade = await broker.place_limit_maker(
            MarketType.PERP, Side.SELL,
            Decimal("76500"), Decimal("0.01"),
        )
        assert trade.is_maker is True
        assert trade.price == Decimal("76500")
        assert trade.quantity == Decimal("0.01")
        assert trade.order_id.startswith("dry_")
        # 不应调真实 broker
        assert broker.n_place_limit_maker == 1

    @pytest.mark.asyncio
    async def test_dry_run_unwind(self) -> None:
        adapter = _mock_adapter()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=True)
        trade = await broker.place_market_unwind(
            MarketType.PERP, Side.BUY, Decimal("0.01"),
        )
        assert trade.is_maker is False
        assert broker.n_market_unwind == 1

    @pytest.mark.asyncio
    async def test_dry_run_cancel_no_op(self) -> None:
        adapter = _mock_adapter()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=True)
        await broker.cancel_order("some_id")
        adapter.cancel_order.assert_not_called()


# ============================================================
# LIVE: LIMIT_MAKER 成交
# ============================================================

class TestLiveLimitMakerSuccess:
    @pytest.mark.asyncio
    async def test_limit_maker_filled(self, monkeypatch) -> None:
        adapter = _mock_adapter()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        # mock LiveBroker.execute → 返回成功 result
        from app.execution.paper_broker import OrderRequest, OrderResult
        from app.exchanges.models import InstrumentType, Side as ExSide, Symbol
        fake_req = OrderRequest(
            symbol=Symbol(base="BTC", quote="USDT"),
            side=ExSide.SELL, size=Decimal("0.01"),
            reference_price=Decimal("76500"), exchange="binance",
            instrument_type=InstrumentType.PERPETUAL,
        )
        fake_result = OrderResult(
            request=fake_req, filled=True, avg_price=Decimal("76500"),
            filled_size=Decimal("0.01"), fees=Decimal("0.153"),
            slippage_bps=Decimal("0"),
        )
        fake_result.order_id = "live123"  # type: ignore
        broker._broker.execute = AsyncMock(return_value=fake_result)

        trade = await broker.place_limit_maker(
            MarketType.PERP, Side.SELL,
            Decimal("76500"), Decimal("0.01"),
        )
        assert trade.is_maker is True
        assert trade.price == Decimal("76500")
        assert trade.order_id == "live123"
        assert trade.fee == Decimal("0.153")


# ============================================================
# LIVE: LIMIT_MAKER 被 reject (post_only 穿越)
# ============================================================

class TestLiveLimitMakerReject:
    @pytest.mark.asyncio
    async def test_post_only_reject_raises_reject_error(self) -> None:
        adapter = _mock_adapter()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        broker._broker.execute = AsyncMock(side_effect=Exception("binance -2010 would match"))

        with pytest.raises(RejectError) as ei:
            await broker.place_limit_maker(
                MarketType.PERP, Side.SELL,
                Decimal("76500"), Decimal("0.01"),
            )
        assert "post_only_crossed" in ei.value.reason
        assert broker.n_reject == 1

    @pytest.mark.asyncio
    async def test_other_exception_not_wrapped(self) -> None:
        adapter = _mock_adapter()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        broker._broker.execute = AsyncMock(side_effect=RuntimeError("timeout"))

        with pytest.raises(RuntimeError) as ei:
            await broker.place_limit_maker(
                MarketType.PERP, Side.SELL,
                Decimal("76500"), Decimal("0.01"),
            )
        assert "timeout" in str(ei.value)
        assert broker.n_reject == 0


# ============================================================
# LIVE: market unwind
# ============================================================

class TestLiveMarketUnwind:
    @pytest.mark.asyncio
    async def test_unwind_perp_uses_reduce_only(self) -> None:
        adapter = _mock_adapter()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        from app.execution.paper_broker import OrderRequest, OrderResult
        captured = {}
        async def capture(req):
            captured["req"] = req
            return OrderResult(
                request=req, filled=True, avg_price=Decimal("76800"),
                filled_size=Decimal("0.01"), fees=Decimal("0.38"),
                slippage_bps=Decimal("0"),
            )
        broker._broker.execute = capture

        trade = await broker.place_market_unwind(
            MarketType.PERP, Side.BUY, Decimal("0.01"),
        )
        assert captured["req"].reduce_only is True
        assert trade.price == Decimal("76800")
        assert trade.is_maker is False

    @pytest.mark.asyncio
    async def test_unwind_spot_no_reduce_only(self) -> None:
        adapter = _mock_adapter()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        from app.execution.paper_broker import OrderResult
        captured = {}
        async def capture(req):
            captured["req"] = req
            return OrderResult(
                request=req, filled=True, avg_price=Decimal("76800"),
                filled_size=Decimal("0.01"), fees=Decimal("0.77"),
                slippage_bps=Decimal("0"),
            )
        broker._broker.execute = capture

        await broker.place_market_unwind(
            MarketType.SPOT, Side.SELL, Decimal("0.01"),
        )
        assert captured["req"].reduce_only is False


# ============================================================
# cancel + fetch_open_orders
# ============================================================

class TestCancelAndFetch:
    @pytest.mark.asyncio
    async def test_cancel_first_try_succeeds(self) -> None:
        # Symbol 没 instrument_type 区分, broker 都试 spot 再 perp; 第 1 次成功
        adapter = _mock_adapter()
        adapter.cancel_order = AsyncMock()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        await broker.cancel_order("oid_x")
        assert broker.n_cancel == 1
        assert adapter.cancel_order.call_count == 1

    @pytest.mark.asyncio
    async def test_cancel_not_found_raises(self) -> None:
        adapter = _mock_adapter()
        adapter.cancel_order = AsyncMock(side_effect=Exception("not_found"))
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        with pytest.raises(RuntimeError, match="cancel_order_not_found"):
            await broker.cancel_order("nope")

    @pytest.mark.asyncio
    async def test_fetch_filters_dgr_prefix(self) -> None:
        adapter = _mock_adapter()
        adapter.fetch_open_orders = AsyncMock(side_effect=[
            [{"clientOrderId": "dgr_111", "id": "spot1"},
             {"clientOrderId": "other_222", "id": "other1"}],
            [{"clientOrderId": "dgr_333", "id": "perp1"}],
        ])
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        orders = await broker.fetch_open_orders()
        ids = [o.get("clientOrderId") for o in orders]
        assert "dgr_111" in ids
        assert "dgr_333" in ids
        assert "other_222" not in ids


# ============================================================
# balance / position
# ============================================================

class TestBalancePosition:
    @pytest.mark.asyncio
    async def test_spot_balance(self) -> None:
        adapter = _mock_adapter()
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        b = await broker.get_spot_balance("BTC")
        assert b == Decimal("0.1")

    @pytest.mark.asyncio
    async def test_perp_position_short_negative(self) -> None:
        adapter = _mock_adapter()
        adapter.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDT:USDT", "contracts": 0.1, "side": "short"},
        ])
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        pos = await broker.get_perp_position("BTC/USDT:USDT")
        assert pos == Decimal("-0.1")

    @pytest.mark.asyncio
    async def test_perp_position_long_positive(self) -> None:
        adapter = _mock_adapter()
        adapter.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDT:USDT", "contracts": 0.05, "side": "long"},
        ])
        broker = DgrBtcBrokerAdapter(adapter=adapter, dry_run=False)
        pos = await broker.get_perp_position("BTC/USDT:USDT")
        assert pos == Decimal("0.05")
