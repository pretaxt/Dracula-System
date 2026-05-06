"""单元测试 — exchanges/models.py"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.exchanges.models import (
    FundingRate,
    OrderBook,
    Side,
    Symbol,
    Ticker,
)


# ---------------------------------------------------------------------------
# Symbol
# ---------------------------------------------------------------------------


class TestSymbol:
    def test_str(self):
        assert str(Symbol("BTC", "USDT")) == "BTC/USDT"

    def test_to_ccxt(self):
        assert Symbol("BTC", "USDT").to_ccxt() == "BTC/USDT"

    def test_to_binance_spot(self):
        assert Symbol("BTC", "USDT").to_binance_spot() == "BTCUSDT"

    def test_from_ccxt_standard(self):
        sym = Symbol.from_ccxt("BTC/USDT")
        assert sym.base == "BTC"
        assert sym.quote == "USDT"

    def test_from_ccxt_linear_perp(self):
        # "BTC/USDT:USDT" — CCXT linear perpetual format
        sym = Symbol.from_ccxt("BTC/USDT:USDT")
        assert sym.base == "BTC"
        assert sym.quote == "USDT"

    def test_from_ccxt_invalid_raises(self):
        with pytest.raises(ValueError):
            Symbol.from_ccxt("BTCUSDT")

    def test_equality(self):
        assert Symbol("BTC", "USDT") == Symbol("BTC", "USDT")

    def test_hashable(self):
        s = {Symbol("BTC", "USDT"), Symbol("BTC", "USDT"), Symbol("ETH", "USDT")}
        assert len(s) == 2

    def test_side_opposite_buy(self):
        assert Side.BUY.opposite() == Side.SELL

    def test_side_opposite_sell(self):
        assert Side.SELL.opposite() == Side.BUY


# ---------------------------------------------------------------------------
# Ticker
# ---------------------------------------------------------------------------


class TestTicker:
    def _make(self, bid="60000", ask="60010"):
        return Ticker(
            symbol=Symbol("BTC", "USDT"),
            bid=Decimal(bid),
            ask=Decimal(ask),
            last=Decimal("60005"),
            volume_24h=Decimal("1000000"),
            timestamp=1700000000000,
        )

    def test_spread_bps(self):
        t = self._make("60000", "60010")
        # spread = 10 / 60000 * 10000 ≈ 1.667 bps
        assert Decimal("1.6") < t.spread_bps < Decimal("1.7")

    def test_mid(self):
        t = self._make("60000", "60010")
        assert t.mid == Decimal("60005")

    def test_spread_zero_bid_returns_zero(self):
        t = self._make("0", "100")
        assert t.spread_bps == Decimal("0")


# ---------------------------------------------------------------------------
# OrderBook
# ---------------------------------------------------------------------------


class TestOrderBook:
    def _make(self):
        return OrderBook(
            symbol=Symbol("BTC", "USDT"),
            bids=[
                (Decimal("59990"), Decimal("1")),
                (Decimal("59980"), Decimal("2")),
            ],
            asks=[
                (Decimal("60000"), Decimal("1")),
                (Decimal("60010"), Decimal("2")),
            ],
            timestamp=1700000000000,
        )

    def test_depth_usd_bids(self):
        ob = self._make()
        bid_d, _ = ob.depth_usd(2)
        # 59990*1 + 59980*2 = 179950
        assert bid_d == Decimal("179950")

    def test_depth_usd_asks(self):
        ob = self._make()
        _, ask_d = ob.depth_usd(2)
        # 60000*1 + 60010*2 = 180020
        assert ask_d == Decimal("180020")

    def test_spread_bps(self):
        ob = self._make()
        # (60000 - 59990) / 59990 * 10000 ≈ 1.667 bps
        spread = ob.spread_bps()
        assert Decimal("1.6") < spread < Decimal("1.7")

    def test_spread_empty_returns_max(self):
        ob = OrderBook(
            symbol=Symbol("BTC", "USDT"),
            bids=[],
            asks=[],
            timestamp=0,
        )
        assert ob.spread_bps() == Decimal("9999")


# ---------------------------------------------------------------------------
# FundingRate
# ---------------------------------------------------------------------------


class TestFundingRate:
    def test_apr_8h_settlement(self):
        fr = FundingRate(
            symbol=Symbol("BTC", "USDT"),
            exchange="binance",
            rate=Decimal("0.0001"),       # 0.01% per 8h
            next_funding_time=1700064000000,
            funding_interval_hours=8,
        )
        # APR = 0.0001 * (24/8) * 365 = 0.0001 * 1095 = 0.1095
        assert fr.apr == Decimal("0.1095")

    def test_apr_1h_settlement(self):
        fr = FundingRate(
            symbol=Symbol("BTC", "USDT"),
            exchange="hyperliquid",
            rate=Decimal("0.00005"),      # 0.005% per 1h
            next_funding_time=1700064000000,
            funding_interval_hours=1,
        )
        # APR = 0.00005 * 24 * 365 = 0.00005 * 8760 = 0.438
        assert fr.apr == Decimal("0.438")

    def test_is_positive_true(self):
        fr = FundingRate(
            symbol=Symbol("BTC", "USDT"),
            exchange="binance",
            rate=Decimal("0.0001"),
            next_funding_time=0,
            funding_interval_hours=8,
        )
        assert fr.is_positive is True

    def test_is_positive_false_when_negative(self):
        fr = FundingRate(
            symbol=Symbol("BTC", "USDT"),
            exchange="binance",
            rate=Decimal("-0.0001"),
            next_funding_time=0,
            funding_interval_hours=8,
        )
        assert fr.is_positive is False

    def test_is_positive_false_when_zero(self):
        fr = FundingRate(
            symbol=Symbol("BTC", "USDT"),
            exchange="binance",
            rate=Decimal("0"),
            next_funding_time=0,
            funding_interval_hours=8,
        )
        assert fr.is_positive is False
