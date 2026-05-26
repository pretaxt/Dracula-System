"""Tests for maker_reprice (Phase E.2)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

import pytest

from app.strategies.dgr_btc.maker_reprice import (
    MakerReprice,
    RejectError,
    RepriceOutcome,
)
from app.strategies.dgr_btc.strategy_core import OrderIntent
from app.strategies.dgr_btc.types import MarketType, Side, Trade


def _make_intent(
    market: MarketType = MarketType.PERP,
    side: Side = Side.SELL,
    price: float = 76000.0,
    qty: float = 0.002,
) -> OrderIntent:
    return OrderIntent(
        market=market,
        side=side,
        price=Decimal(str(price)),
        quantity=Decimal(str(qty)),
        grid_level=Decimal(str(price)),
        reason="grid_up_76000.00",
    )


def _make_trade(price: Decimal, qty: Decimal, market: MarketType, side: Side) -> Trade:
    return Trade(
        trade_id=f"t_{uuid.uuid4().hex[:8]}",
        order_id=f"o_{uuid.uuid4().hex[:8]}",
        symbol="BTC/USDT" if market == MarketType.SPOT else "BTC/USDT:USDT",
        market=market,
        side=side,
        price=price,
        quantity=qty,
        fee=price * qty * Decimal("0.0002"),
        is_maker=True,
        timestamp=datetime.now(timezone.utc),
        grid_level=price,
    )


class FakeBroker:
    def __init__(self, reject_sequence: list[bool]) -> None:
        self.reject_sequence = list(reject_sequence)
        self.call_count = 0
        self.prices_seen: list[Decimal] = []

    async def place_limit_maker(
        self,
        market: MarketType,
        side: Side,
        price: Decimal,
        quantity: Decimal,
    ) -> Trade:
        self.call_count += 1
        self.prices_seen.append(price)
        if self.reject_sequence and self.reject_sequence.pop(0):
            raise RejectError(f"post_only_crossed @ {price}")
        return _make_trade(price, quantity, market, side)


class FakeBrokerException:
    async def place_limit_maker(self, **kwargs: Any) -> Trade:
        raise RuntimeError("binance API timeout")


# ============================================================
# happy path
# ============================================================

class TestMakerRepriceHappy:
    @pytest.mark.asyncio
    async def test_filled_first_attempt(self) -> None:
        broker = FakeBroker(reject_sequence=[False])
        repricer = MakerReprice(broker)
        intent = _make_intent()
        result = await repricer.place(intent)

        assert result.outcome == RepriceOutcome.FILLED_FIRST_TRY
        assert result.attempts == 1
        assert result.trade is not None
        assert result.trade.price == Decimal("76000.0")
        assert broker.call_count == 1
        assert repricer.n_filled_first == 1


# ============================================================
# reprice path
# ============================================================

class TestMakerRepriceWithReprice:
    @pytest.mark.asyncio
    async def test_filled_after_one_reprice(self) -> None:
        broker = FakeBroker(reject_sequence=[True, False])
        repricer = MakerReprice(broker, reprice_delay_ms=0)
        intent = _make_intent(side=Side.SELL, price=76000.0)
        result = await repricer.place(intent)

        assert result.outcome == RepriceOutcome.FILLED_AFTER_REPRICE
        assert result.attempts == 2
        assert result.trade is not None
        # SELL reject → 价格上推 1 tick (perp tick=0.1)
        assert broker.prices_seen == [Decimal("76000.0"), Decimal("76000.1")]
        assert len(result.reject_history) == 1
        assert repricer.n_filled_after_reprice == 1

    @pytest.mark.asyncio
    async def test_buy_side_reprice_decreases_price(self) -> None:
        broker = FakeBroker(reject_sequence=[True, False])
        repricer = MakerReprice(broker, reprice_delay_ms=0)
        intent = _make_intent(side=Side.BUY, price=76000.0)
        result = await repricer.place(intent)

        assert result.outcome == RepriceOutcome.FILLED_AFTER_REPRICE
        # BUY reject → 价格下推
        assert broker.prices_seen == [Decimal("76000.0"), Decimal("75999.9")]

    @pytest.mark.asyncio
    async def test_spot_tick_size(self) -> None:
        broker = FakeBroker(reject_sequence=[True, False])
        repricer = MakerReprice(broker, reprice_delay_ms=0)
        intent = _make_intent(
            market=MarketType.SPOT, side=Side.SELL, price=76000.00
        )
        result = await repricer.place(intent)
        assert result.outcome == RepriceOutcome.FILLED_AFTER_REPRICE
        # spot tick = 0.01
        assert broker.prices_seen == [Decimal("76000.0"), Decimal("76000.01")]


# ============================================================
# give-up path
# ============================================================

class TestMakerRepriceGiveUp:
    @pytest.mark.asyncio
    async def test_give_up_after_3_rejects(self) -> None:
        broker = FakeBroker(reject_sequence=[True, True, True])
        repricer = MakerReprice(broker, max_attempts=3, reprice_delay_ms=0)
        intent = _make_intent(side=Side.SELL, price=76000.0)
        result = await repricer.place(intent)

        assert result.outcome == RepriceOutcome.GIVE_UP_AFTER_MAX_ATTEMPTS
        assert result.attempts == 3
        assert result.trade is None
        assert len(result.reject_history) == 3
        assert broker.call_count == 3
        # 价格序列: 76000.0, 76000.1, 76000.2
        assert broker.prices_seen == [
            Decimal("76000.0"),
            Decimal("76000.1"),
            Decimal("76000.2"),
        ]
        assert repricer.n_give_up == 1
        assert repricer.n_filled_first == 0

    @pytest.mark.asyncio
    async def test_custom_max_attempts(self) -> None:
        broker = FakeBroker(reject_sequence=[True, True, True, True, True])
        repricer = MakerReprice(broker, max_attempts=5, reprice_delay_ms=0)
        result = await repricer.place(_make_intent())
        assert result.outcome == RepriceOutcome.GIVE_UP_AFTER_MAX_ATTEMPTS
        assert result.attempts == 5
        assert len(result.reject_history) == 5


# ============================================================
# broker exception path (non-reject errors)
# ============================================================

class TestMakerRepriceException:
    @pytest.mark.asyncio
    async def test_broker_exception_returns_early(self) -> None:
        broker = FakeBrokerException()
        repricer = MakerReprice(broker, reprice_delay_ms=0)
        result = await repricer.place(_make_intent())
        assert result.outcome == RepriceOutcome.BROKER_EXCEPTION
        assert result.attempts == 1
        assert result.trade is None


# ============================================================
# stats
# ============================================================

class TestMakerRepriceStats:
    @pytest.mark.asyncio
    async def test_stats_aggregate(self) -> None:
        broker = FakeBroker(reject_sequence=[False, True, False, True, True, True])
        repricer = MakerReprice(broker, max_attempts=3, reprice_delay_ms=0)
        # call 1: first try fills (False)
        await repricer.place(_make_intent())
        # call 2: reject then fill (True, False)
        await repricer.place(_make_intent())
        # call 3: 3 rejects (True, True, True)
        await repricer.place(_make_intent())
        s = repricer.stats()
        assert s["n_calls"] == 3
        assert s["n_filled_first"] == 1
        assert s["n_filled_after_reprice"] == 1
        assert s["n_give_up"] == 1
