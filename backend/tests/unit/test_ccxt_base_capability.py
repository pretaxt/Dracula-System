"""单元测试 — ccxt_base.place_order 参数 capability 双层防护（R5/X6）。

确认 spot leg 永远不会传 reduceOnly / positionSide，即使调用方误传。
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exchanges.cex.binance import BinanceAdapter
from app.exchanges.models import (
    InstrumentType, Order, OrderStatus, OrderType, Side, Symbol,
)

BTC = Symbol("BTC", "USDT")


def _make_test_adapter() -> BinanceAdapter:
    """构造 BinanceAdapter，spot client + perp client 都 mock。"""
    adapter = BinanceAdapter(api_key="test", api_secret="test")
    spot_client = MagicMock()
    perp_client = MagicMock()
    spot_client.create_order = AsyncMock(return_value={
        "id": "X", "clientOrderId": "", "symbol": "BTC/USDT",
        "side": "buy", "type": "market", "amount": 0.01, "filled": 0.01,
        "price": 0, "average": 60000, "status": "closed", "timestamp": 1_700_000_000_000,
    })
    perp_client.create_order = AsyncMock(return_value={
        "id": "Y", "clientOrderId": "", "symbol": "BTC/USDT",
        "side": "sell", "type": "market", "amount": 0.01, "filled": 0.01,
        "price": 0, "average": 60000, "status": "closed", "timestamp": 1_700_000_000_000,
    })
    spot_client.set_leverage = AsyncMock()
    perp_client.set_leverage = AsyncMock()
    adapter._clients = {
        InstrumentType.SPOT: spot_client,
        InstrumentType.PERPETUAL: perp_client,
    }
    return adapter


class TestSpotIgnoresReduceOnly:
    """R5/X6: ccxt_base.place_order spot 调用 reduce_only=True 必须在 params 里被屏蔽。"""

    @pytest.mark.asyncio
    async def test_spot_reduce_only_dropped(self):
        adapter = _make_test_adapter()
        await adapter.place_order(
            symbol=BTC,
            instrument=InstrumentType.SPOT,
            side=Side.SELL,
            order_type=OrderType.MARKET,
            size=Decimal("0.01"),
            reduce_only=True,  # ← 调用方误传
        )
        spot_client = adapter._clients[InstrumentType.SPOT]
        call = spot_client.create_order.await_args
        params = call.args[5] if len(call.args) > 5 else call.kwargs.get("params", {})
        assert "reduceOnly" not in params, (
            f"spot create_order params 不能含 reduceOnly，实际 params={params}"
        )

    @pytest.mark.asyncio
    async def test_perp_reduce_only_kept(self):
        adapter = _make_test_adapter()
        await adapter.place_order(
            symbol=BTC,
            instrument=InstrumentType.PERPETUAL,
            side=Side.BUY,
            order_type=OrderType.MARKET,
            size=Decimal("0.01"),
            reduce_only=True,
        )
        perp_client = adapter._clients[InstrumentType.PERPETUAL]
        call = perp_client.create_order.await_args
        params = call.args[5] if len(call.args) > 5 else call.kwargs.get("params", {})
        assert params.get("reduceOnly") is True

    @pytest.mark.asyncio
    async def test_spot_position_side_dropped(self):
        adapter = _make_test_adapter()
        await adapter.place_order(
            symbol=BTC,
            instrument=InstrumentType.SPOT,
            side=Side.BUY,
            order_type=OrderType.MARKET,
            size=Decimal("0.01"),
            position_side="LONG",  # ← 调用方误传 spot
        )
        spot_client = adapter._clients[InstrumentType.SPOT]
        call = spot_client.create_order.await_args
        params = call.args[5] if len(call.args) > 5 else call.kwargs.get("params", {})
        assert "positionSide" not in params, (
            f"spot create_order params 不能含 positionSide，实际 params={params}"
        )
