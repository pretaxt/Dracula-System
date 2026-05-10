"""单元测试 — strategies/perp_basis/paper_trading.py（Phase C 完整版）。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exchanges.models import InstrumentType, Side
from app.execution.paper_broker import OrderResult
from app.strategies.perp_basis.paper_trading import PerpBasisPaperSession


def _make_opp(symbol="BTC/USDT", long_ex="okx", short_ex="binance",
              long_apr=5, short_apr=60, price=60000):
    o = MagicMock()
    o.symbol = symbol
    o.long_exchange = long_ex
    o.short_exchange = short_ex
    o.long_apr_pct = Decimal(str(long_apr))
    o.short_apr_pct = Decimal(str(short_apr))
    o.long_perp_price = Decimal(str(price))
    o.short_perp_price = Decimal(str(price + 5))
    o.diff_apr_pct = Decimal(str(short_apr - long_apr))
    return o


def _make_broker(exchange_id="binance", lev="5"):
    broker = MagicMock()
    broker._perp_leverage = Decimal(lev)
    perp_client = MagicMock()
    perp_client.fetch_balance = AsyncMock(
        return_value={"total": {"USDT": "1000"}, "free": {"USDT": "1000"}},
    )
    broker._adapter = MagicMock()
    broker._adapter.exchange_id = exchange_id
    broker._adapter._clients = {InstrumentType.PERPETUAL: perp_client}
    broker._ensure_perp_margin = AsyncMock()
    broker.execute = AsyncMock(return_value=OrderResult(
        request=MagicMock(),
        filled=True,
        avg_price=Decimal("60000"),
        filled_size=Decimal("0.001"),
        fees=Decimal("0.024"),
        slippage_bps=Decimal("0"),
        filled_at=datetime.now(timezone.utc),
    ))
    return broker


def _build_sess(scanner, brokers, **kw):
    """构造 session 并 mock DB save 避免连真实 postgres。"""
    sess = PerpBasisPaperSession(
        scanner=scanner, brokers=brokers,
        notional_per_position=Decimal("50"),
        min_diff_apr_pct=Decimal("50"),
        **kw,
    )
    sess._manager.save = AsyncMock()
    return sess


class TestThresholdGate:
    @pytest.mark.asyncio
    async def test_below_threshold_skipped(self):
        scanner = MagicMock()
        scanner.scan = MagicMock(return_value=[_make_opp(short_apr=20)])
        sess = _build_sess(scanner, {"binance": _make_broker("binance"),
                                     "okx": _make_broker("okx")})
        await sess._tick()
        assert len(sess.open_positions) == 0

    @pytest.mark.asyncio
    async def test_above_threshold_opens(self):
        scanner = MagicMock()
        scanner.scan = MagicMock(return_value=[_make_opp()])
        sess = _build_sess(scanner, {"binance": _make_broker("binance"),
                                     "okx": _make_broker("okx")})
        await sess._tick()
        assert len(sess.open_positions) == 1
        pos = sess.open_positions[0]
        # 验证两腿 exchange（取代旧 CrossExchangePosition.long_exchange）
        long_leg = next((l for l in pos.legs if l.side == Side.BUY), None)
        short_leg = next((l for l in pos.legs if l.side == Side.SELL), None)
        assert long_leg is not None and short_leg is not None
        assert long_leg.exchange == "okx"
        assert short_leg.exchange == "binance"


class TestNoBrokerSkip:
    @pytest.mark.asyncio
    async def test_missing_broker_skipped(self):
        scanner = MagicMock()
        scanner.scan = MagicMock(return_value=[_make_opp(long_ex="bybit")])
        sess = _build_sess(scanner, {"binance": _make_broker("binance")})
        await sess._tick()
        assert len(sess.open_positions) == 0


class TestCrossUnwind:
    @pytest.mark.asyncio
    async def test_long_fail_unwinds_short(self):
        scanner = MagicMock()
        scanner.scan = MagicMock(return_value=[_make_opp()])
        b_long = _make_broker("okx")
        b_short = _make_broker("binance")
        b_long.execute = AsyncMock(side_effect=RuntimeError("long failed"))
        sess = _build_sess(scanner, {"binance": b_short, "okx": b_long})
        await sess._tick()
        assert len(sess.open_positions) == 0
        # short broker 调 2 次（开 + unwind）
        assert b_short.execute.await_count == 2


class TestPreflight:
    @pytest.mark.asyncio
    async def test_perp_balance_zero_skip(self):
        scanner = MagicMock()
        scanner.scan = MagicMock(return_value=[_make_opp()])
        b1 = _make_broker("binance")
        b2 = _make_broker("okx")
        b1._adapter._clients[InstrumentType.PERPETUAL].fetch_balance = AsyncMock(
            return_value={"total": {"USDT": "0"}, "free": {"USDT": "0"}},
        )
        sess = _build_sess(scanner, {"binance": b1, "okx": b2})
        await sess._tick()
        assert len(sess.open_positions) == 0
        b1.execute.assert_not_awaited()
        b2.execute.assert_not_awaited()


class TestManualClose:
    @pytest.mark.asyncio
    async def test_close_position_via_api(self):
        scanner = MagicMock()
        scanner.scan = MagicMock(return_value=[_make_opp()])
        b1 = _make_broker("binance")
        b2 = _make_broker("okx")
        sess = _build_sess(scanner, {"binance": b1, "okx": b2})
        await sess._tick()
        assert len(sess.open_positions) == 1
        pos_id = sess.open_positions[0].id
        await sess.close_position(pos_id, reason="manual")
        # close 后无 OPEN
        from app.risk.models import PositionStatus
        pos = sess._manager.get(pos_id)
        assert pos.status == PositionStatus.CLOSED
