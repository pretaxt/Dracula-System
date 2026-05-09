"""单元测试 — strategies/spot_perp_basis/paper_trading.py D.1 live mode。

覆盖：
  - notes 编解码（前向兼容旧记录）
  - _symbol_from_pair
  - 构造校验：live_mode 必须有 brokers
  - _open_live：premium 双腿成功 / no broker / broker 抛错
  - _close_live：成功 / no broker / 元数据缺失 / spot 平仓失败
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.execution.paper_broker import OrderResult
from app.strategies.spot_perp_basis import paper_trading as pt
from app.strategies.spot_perp_basis.paper_trading import (
    SpotPerpPaperSession,
    SpotPerpStrategyConfig,
    _decode_notes,
    _encode_notes,
    _symbol_from_pair,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _opp(symbol="BTC/USDT", direction="premium", basis_pct="0.20",
         spot=50000, perp=50100, exchange="binance"):
    return SimpleNamespace(
        symbol=symbol,
        exchange=exchange,
        spot_price=Decimal(str(spot)),
        perp_price=Decimal(str(perp)),
        basis_abs=Decimal(str(perp)) - Decimal(str(spot)),
        basis_pct=Decimal(str(basis_pct)),
        direction=direction,
        timestamp_ms=0,
    )


def _result(filled_size="0.001", avg_price="50000", fees="0.05") -> OrderResult:
    req = MagicMock()
    return OrderResult(
        request=req,
        filled=True,
        avg_price=Decimal(str(avg_price)),
        filled_size=Decimal(str(filled_size)),
        fees=Decimal(str(fees)),
        slippage_bps=Decimal("0"),
    )


def _session(*, live_mode=False, brokers=None, notional=None):
    runner = MagicMock()
    runner.latest_opportunities = []
    return SpotPerpPaperSession(
        runner=runner,
        live_mode=live_mode,
        brokers=brokers,
        notional_per_position=notional,
    )


# ---------------------------------------------------------------------------
# 编解码
# ---------------------------------------------------------------------------


class TestNotesEncoding:
    def test_encode_no_meta(self):
        assert _encode_notes("BTC/USDT") == "BTC/USDT"
        assert _encode_notes("BTC/USDT", None) == "BTC/USDT"
        assert _encode_notes("BTC/USDT", {}) == "BTC/USDT"

    def test_encode_with_meta_roundtrip(self):
        meta = {"spot_size": "0.01", "entry_spot_px": "50000"}
        encoded = _encode_notes("BTC/USDT", meta)
        sym, decoded = _decode_notes(encoded)
        assert sym == "BTC/USDT"
        assert decoded == meta

    def test_decode_legacy_symbol_only(self):
        sym, meta = _decode_notes("BTC/USDT")
        assert sym == "BTC/USDT"
        assert meta is None

    def test_decode_empty(self):
        assert _decode_notes("") == ("", None)

    def test_decode_invalid_json_falls_back_to_symbol(self):
        sym, meta = _decode_notes("BTC/USDT\nnot a json")
        assert sym == "BTC/USDT"
        assert meta is None

    def test_decode_non_dict_json(self):
        sym, meta = _decode_notes('BTC/USDT\n"just a string"')
        assert sym == "BTC/USDT"
        assert meta is None


class TestSymbolFromPair:
    def test_basic(self):
        s = _symbol_from_pair("BTC/USDT")
        assert s.base == "BTC" and s.quote == "USDT"

    def test_default_quote_when_missing(self):
        s = _symbol_from_pair("BTC")
        assert s.base == "BTC" and s.quote == "USDT"


# ---------------------------------------------------------------------------
# 构造校验
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_live_mode_without_brokers_raises(self):
        with pytest.raises(ValueError, match="brokers"):
            _session(live_mode=True, brokers=None)

    def test_live_mode_with_empty_brokers_raises(self):
        with pytest.raises(ValueError):
            _session(live_mode=True, brokers={})

    def test_paper_mode_default(self):
        s = _session()
        assert s.live_mode is False
        assert s._notional == pt.NOTIONAL_PER_POSITION

    def test_notional_override(self):
        s = _session(notional=Decimal("50"))
        assert s._notional == Decimal("50")


# ---------------------------------------------------------------------------
# _open_live
# ---------------------------------------------------------------------------


class TestOpenLive:
    @pytest.mark.asyncio
    async def test_premium_happy_path_returns_meta(self):
        broker = MagicMock()
        broker.execute_pair = AsyncMock(return_value=(
            _result(filled_size="0.0002", avg_price="50000", fees="0.04"),
            _result(filled_size="0.0002", avg_price="50100", fees="0.04"),
        ))
        s = _session(live_mode=True, brokers={"binance": broker},
                     notional=Decimal("10"))
        meta = await s._open_live(_opp(spot=50000, perp=50100))
        assert meta is not None
        assert meta["spot_size"] == "0.0002"
        assert meta["entry_spot_px"] == "50000"
        assert meta["entry_perp_px"] == "50100"
        assert meta["exchange"] == "binance"
        assert "client_id" in meta
        assert Decimal(meta["fees"]) == Decimal("0.08")
        broker.execute_pair.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_broker_for_exchange_returns_none(self):
        broker = MagicMock()
        broker.execute_pair = AsyncMock()
        s = _session(live_mode=True, brokers={"okx": broker})
        meta = await s._open_live(_opp(exchange="binance"))
        assert meta is None
        broker.execute_pair.assert_not_called()

    @pytest.mark.asyncio
    async def test_broker_exception_returns_none(self):
        broker = MagicMock()
        broker.execute_pair = AsyncMock(side_effect=RuntimeError("nope"))
        s = _session(live_mode=True, brokers={"binance": broker})
        meta = await s._open_live(_opp())
        assert meta is None

    @pytest.mark.asyncio
    async def test_zero_spot_price_returns_none(self):
        broker = MagicMock()
        broker.execute_pair = AsyncMock()
        s = _session(live_mode=True, brokers={"binance": broker})
        meta = await s._open_live(_opp(spot=0, perp=10))
        assert meta is None
        broker.execute_pair.assert_not_called()


# ---------------------------------------------------------------------------
# _close_live
# ---------------------------------------------------------------------------


class TestCloseLive:
    @pytest.mark.asyncio
    async def test_close_happy_path(self):
        broker = MagicMock()
        # spot SELL @ 50050, perp BUY @ 50080, fees 0.05 each
        broker.execute = AsyncMock(side_effect=[
            _result(filled_size="0.001", avg_price="50050", fees="0.05"),
            _result(filled_size="0.001", avg_price="50080", fees="0.05"),
        ])
        s = _session(live_mode=True, brokers={"binance": broker})
        result = await s._close_live("BTC/USDT", {
            "exchange": "binance",
            "spot_size": "0.001",
            "perp_size": "0.001",
            "entry_spot_px": "50000",
            "entry_perp_px": "50100",
        })
        assert result is not None
        assert result["close_spot_px"] == "50050"
        assert result["close_perp_px"] == "50080"
        assert Decimal(result["close_fees"]) == Decimal("0.10")
        assert broker.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_close_no_broker_returns_none(self):
        s = _session(live_mode=True, brokers={"binance": MagicMock()})
        out = await s._close_live("BTC/USDT", {"exchange": "okx",
                                                "spot_size": "0.001",
                                                "perp_size": "0.001"})
        assert out is None

    @pytest.mark.asyncio
    async def test_close_missing_meta_size(self):
        broker = MagicMock()
        broker.execute = AsyncMock()
        s = _session(live_mode=True, brokers={"binance": broker})
        out = await s._close_live("BTC/USDT", {"exchange": "binance"})
        assert out is None
        broker.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_spot_failure_returns_none(self):
        broker = MagicMock()
        broker.execute = AsyncMock(side_effect=RuntimeError("spot fail"))
        s = _session(live_mode=True, brokers={"binance": broker})
        out = await s._close_live("BTC/USDT", {
            "exchange": "binance",
            "spot_size": "0.001",
            "perp_size": "0.001",
            "entry_spot_px": "50000",
            "entry_perp_px": "50100",
        })
        assert out is None


class TestRealPnlFromFills:
    """D.2.a 真实成交价 PnL 计算 — premium 双腿 (spot LONG + perp SHORT)。"""

    def test_basis_converges_profitable(self):
        """入场 spot=50000/perp=50100,基差 +0.20%；收敛到 0% 平：spot 涨/perp 跌。"""
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        meta = {
            "spot_size": "0.001", "perp_size": "0.001",
            "entry_spot_px": "50000", "entry_perp_px": "50100",
        }
        # close prices reflect basis convergence: spot 50050, perp 50050
        close = {
            "close_spot_px": "50050", "close_perp_px": "50050",
            "close_fees": "0.04",
        }
        pnl = SpotPerpPaperSession._real_pnl_from_fills(meta, close)
        assert pnl is not None
        # spot 涨 50 × 0.001 = 0.05；perp 跌 50 × 0.001 = 0.05；合计 0.10 - 0.04 fee = 0.06
        assert pnl == Decimal("0.06")

    def test_basis_widens_loss(self):
        meta = {
            "spot_size": "0.001", "perp_size": "0.001",
            "entry_spot_px": "50000", "entry_perp_px": "50100",
        }
        # 基差扩大: spot 跌到 49950, perp 涨到 50200
        close = {
            "close_spot_px": "49950", "close_perp_px": "50200",
            "close_fees": "0.04",
        }
        pnl = SpotPerpPaperSession._real_pnl_from_fills(meta, close)
        assert pnl is not None
        # spot -50 × 0.001 = -0.05；perp -100 × 0.001 = -0.10；-0.15 - 0.04 = -0.19
        assert pnl == Decimal("-0.19")

    def test_missing_meta_returns_none(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        out = SpotPerpPaperSession._real_pnl_from_fills(
            {"spot_size": "0.001"},  # missing entry_spot_px etc.
            {"close_spot_px": "50000", "close_perp_px": "50000"},
        )
        assert out is None

    def test_zero_size_returns_none(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        out = SpotPerpPaperSession._real_pnl_from_fills(
            {"spot_size": "0", "perp_size": "0",
             "entry_spot_px": "50000", "entry_perp_px": "50100"},
            {"close_spot_px": "50050", "close_perp_px": "50050"},
        )
        assert out is None

    def test_invalid_decimal_returns_none(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        out = SpotPerpPaperSession._real_pnl_from_fills(
            {"spot_size": "0.001", "perp_size": "0.001",
             "entry_spot_px": "abc", "entry_perp_px": "50100"},
            {"close_spot_px": "50050", "close_perp_px": "50050"},
        )
        assert out is None

    # --- D.2.c discount 方向 PnL ---

    def test_discount_basis_converges_profitable(self):
        """入场 spot=50100/perp=50000 (vasis -0.20%)，收敛至 0%：spot 跌 / perp 涨。

        discount: SHORT spot + LONG perp。
        spot 跌 50: (50100 - 50050) × 0.001 = +0.05
        perp 涨 50: (50050 - 50000) × 0.001 = +0.05
        合计 +0.10 - 0.04 fees = +0.06
        """
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        meta = {
            "direction": "discount",
            "spot_size": "0.001", "perp_size": "0.001",
            "entry_spot_px": "50100", "entry_perp_px": "50000",
        }
        close = {
            "close_spot_px": "50050", "close_perp_px": "50050",
            "close_fees": "0.04",
        }
        pnl = SpotPerpPaperSession._real_pnl_from_fills(meta, close)
        assert pnl == Decimal("0.06")

    def test_discount_basis_widens_loss(self):
        """discount 入场后基差扩大: spot 涨 / perp 跌 → 双腿均亏。"""
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        meta = {
            "direction": "discount",
            "spot_size": "0.001", "perp_size": "0.001",
            "entry_spot_px": "50100", "entry_perp_px": "50000",
        }
        close = {
            "close_spot_px": "50200", "close_perp_px": "49900",
            "close_fees": "0.04",
        }
        pnl = SpotPerpPaperSession._real_pnl_from_fills(meta, close)
        # spot SHORT 亏 (50100-50200)*0.001 = -0.10；perp LONG 亏 (49900-50000)*0.001 = -0.10
        # 合计 -0.20 - 0.04 = -0.24
        assert pnl == Decimal("-0.24")

    def test_unknown_direction_returns_none(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        out = SpotPerpPaperSession._real_pnl_from_fills(
            {"direction": "sideways",
             "spot_size": "0.001", "perp_size": "0.001",
             "entry_spot_px": "50000", "entry_perp_px": "50000"},
            {"close_spot_px": "50000", "close_perp_px": "50000"},
        )
        assert out is None

    def test_legacy_meta_no_direction_defaults_premium(self):
        """D.1 旧 meta 没有 direction 字段时按 premium 处理（向后兼容）。"""
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        meta = {
            # no direction
            "spot_size": "0.001", "perp_size": "0.001",
            "entry_spot_px": "50000", "entry_perp_px": "50100",
        }
        close = {
            "close_spot_px": "50050", "close_perp_px": "50050",
            "close_fees": "0.04",
        }
        pnl = SpotPerpPaperSession._real_pnl_from_fills(meta, close)
        # 按 premium 算: 0.05 + 0.05 - 0.04 = 0.06
        assert pnl == Decimal("0.06")


class TestOpenLiveDiscount:
    @pytest.mark.asyncio
    async def test_discount_uses_margin_short_spot(self):
        """discount 方向: spot SELL margin (MARGIN_BUY) + perp BUY (long)。"""
        captured = {}

        async def _record_pair(spot_req, perp_req):
            captured["spot"] = spot_req
            captured["perp"] = perp_req
            return (
                _result(filled_size="0.001", avg_price="50100", fees="0.04"),
                _result(filled_size="0.001", avg_price="50000", fees="0.04"),
            )

        broker = MagicMock()
        broker.execute_pair = AsyncMock(side_effect=_record_pair)
        s = _session(live_mode=True, brokers={"binance": broker})
        opp = _opp(direction="discount", spot=50100, perp=50000, basis_pct="-0.20")
        meta = await s._open_live(opp)
        assert meta is not None
        assert meta["direction"] == "discount"
        # spot 腿: SELL + margin
        assert captured["spot"].side.value == "sell"
        assert captured["spot"].margin_mode == "cross"
        assert captured["spot"].side_effect == "MARGIN_BUY"
        # perp 腿: BUY (LONG)
        assert captured["perp"].side.value == "buy"
        assert captured["perp"].margin_mode is None  # perp 不需要 margin
        assert captured["perp"].side_effect is None

    @pytest.mark.asyncio
    async def test_premium_keeps_legacy_path(self):
        """premium 方向行为不变（D.1 兼容）：spot BUY + perp SELL，无 margin 字段。"""
        captured = {}

        async def _record_pair(spot_req, perp_req):
            captured["spot"] = spot_req
            captured["perp"] = perp_req
            return (
                _result(filled_size="0.001", avg_price="50000", fees="0.04"),
                _result(filled_size="0.001", avg_price="50100", fees="0.04"),
            )

        broker = MagicMock()
        broker.execute_pair = AsyncMock(side_effect=_record_pair)
        s = _session(live_mode=True, brokers={"binance": broker})
        opp = _opp(direction="premium")
        meta = await s._open_live(opp)
        assert meta is not None
        assert meta["direction"] == "premium"
        assert captured["spot"].side.value == "buy"
        assert captured["spot"].margin_mode is None
        assert captured["perp"].side.value == "sell"

    @pytest.mark.asyncio
    async def test_okx_discount_uses_cross_no_side_effect(self):
        """OKX UTA discount: cross-margin 自动借/还，不传 side_effect（与 Binance 不同）。"""
        captured = {}

        async def _record_pair(spot_req, perp_req):
            captured["spot"] = spot_req
            captured["perp"] = perp_req
            return (
                _result(filled_size="0.001", avg_price="50100", fees="0.04"),
                _result(filled_size="0.001", avg_price="50000", fees="0.04"),
            )

        broker = MagicMock()
        broker.execute_pair = AsyncMock(side_effect=_record_pair)
        s = _session(live_mode=True, brokers={"okx": broker})
        opp = _opp(direction="discount", spot=50100, perp=50000, basis_pct="-0.20", exchange="okx")
        meta = await s._open_live(opp)
        assert meta is not None, "OKX discount 应实盘开仓（不再硬跳过）"
        assert meta["direction"] == "discount"
        assert meta["exchange"] == "okx"
        # spot 腿: SELL + cross margin（自动借），但 side_effect=None（OKX 无此概念）
        assert captured["spot"].side.value == "sell"
        assert captured["spot"].margin_mode == "cross"
        assert captured["spot"].side_effect is None, "OKX UTA 不应传 sideEffectType"
        # perp 腿: BUY (LONG)
        assert captured["perp"].side.value == "buy"
        assert captured["perp"].position_side == "LONG"


class TestCloseLiveDiscount:
    @pytest.mark.asyncio
    async def test_discount_close_uses_auto_repay(self):
        """discount 平仓: spot BUY + AUTO_REPAY 自动还币 + perp SELL。"""
        captured = []

        async def _record(req):
            captured.append(req)
            return _result(filled_size="0.001", avg_price="50050", fees="0.05")

        broker = MagicMock()
        broker.execute = AsyncMock(side_effect=_record)
        s = _session(live_mode=True, brokers={"binance": broker})
        out = await s._close_live("BTC/USDT", {
            "exchange": "binance",
            "direction": "discount",
            "spot_size": "0.001",
            "perp_size": "0.001",
            "entry_spot_px": "50100",
            "entry_perp_px": "50000",
        })
        assert out is not None
        # 第一笔 spot BUY + AUTO_REPAY
        assert captured[0].side.value == "buy"
        assert captured[0].margin_mode == "cross"
        assert captured[0].side_effect == "AUTO_REPAY"
        # 第二笔 perp SELL reduce_only
        assert captured[1].side.value == "sell"
        assert captured[1].instrument_type.value == "perpetual"
        assert captured[1].reduce_only is True

    @pytest.mark.asyncio
    async def test_premium_close_legacy_path(self):
        """premium 平仓不带 margin（D.1 行为）。"""
        captured = []

        async def _record(req):
            captured.append(req)
            return _result(filled_size="0.001", avg_price="50050", fees="0.05")

        broker = MagicMock()
        broker.execute = AsyncMock(side_effect=_record)
        s = _session(live_mode=True, brokers={"binance": broker})
        out = await s._close_live("BTC/USDT", {
            "exchange": "binance",
            "direction": "premium",
            "spot_size": "0.001",
            "perp_size": "0.001",
            "entry_spot_px": "50000",
            "entry_perp_px": "50100",
        })
        assert out is not None
        # spot SELL + reduce_only + 无 margin
        assert captured[0].side.value == "sell"
        assert captured[0].margin_mode is None
        # perp BUY + reduce_only
        assert captured[1].side.value == "buy"
        assert captured[1].reduce_only is True

    @pytest.mark.asyncio
    async def test_okx_discount_close_no_auto_repay(self):
        """OKX UTA discount 平仓: cross-margin 买回时自动减债，不传 AUTO_REPAY。"""
        captured = []

        async def _record(req):
            captured.append(req)
            return _result(filled_size="0.001", avg_price="50050", fees="0.05")

        broker = MagicMock()
        broker.execute = AsyncMock(side_effect=_record)
        s = _session(live_mode=True, brokers={"okx": broker})
        out = await s._close_live("BTC/USDT", {
            "exchange": "okx",
            "direction": "discount",
            "spot_size": "0.001",
            "perp_size": "0.001",
            "entry_spot_px": "50100",
            "entry_perp_px": "50000",
        })
        assert out is not None
        # spot BUY + cross margin（自动还债），无 AUTO_REPAY
        assert captured[0].side.value == "buy"
        assert captured[0].margin_mode == "cross"
        assert captured[0].side_effect is None, "OKX UTA 不应传 sideEffectType"
        # perp SELL reduce_only
        assert captured[1].side.value == "sell"
        assert captured[1].reduce_only is True

    @pytest.mark.asyncio
    async def test_unknown_direction_returns_none(self):
        broker = MagicMock()
        broker.execute = AsyncMock()
        s = _session(live_mode=True, brokers={"binance": broker})
        out = await s._close_live("BTC/USDT", {
            "exchange": "binance",
            "direction": "sideways",
            "spot_size": "0.001",
            "perp_size": "0.001",
            "entry_spot_px": "50000",
            "entry_perp_px": "50100",
        })
        assert out is None
        broker.execute.assert_not_called()


class TestRealPnlWithFundingBorrow:
    """D.2.b — funding + borrow 加入 PnL 公式。"""

    def test_premium_funding_received_increases_pnl(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        meta = {
            "direction": "premium",
            "spot_size": "0.001", "perp_size": "0.001",
            "entry_spot_px": "50000", "entry_perp_px": "50100",
        }
        # 持仓收到资金费 0.05; 基差从 0.20% 收敛到 0
        close = {
            "close_spot_px": "50050", "close_perp_px": "50050",
            "close_fees": "0.04",
            "funding_received": "0.05",
        }
        pnl = SpotPerpPaperSession._real_pnl_from_fills(meta, close)
        # 0.05 spot + 0.05 perp + 0.05 funding - 0.04 fees = 0.11
        assert pnl == Decimal("0.11")

    def test_discount_borrow_interest_decreases_pnl(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        meta = {
            "direction": "discount",
            "spot_size": "0.001", "perp_size": "0.001",
            "entry_spot_px": "50100", "entry_perp_px": "50000",
        }
        # 持仓 12h × 0.0001%/h × $50 = $0.0006 借币利息
        close = {
            "close_spot_px": "50050", "close_perp_px": "50050",
            "close_fees": "0.04",
            "funding_received": "0.03",
            "borrow_interest": "0.0006",
        }
        pnl = SpotPerpPaperSession._real_pnl_from_fills(meta, close)
        # 0.05 spot + 0.05 perp + 0.03 funding - 0.04 fees - 0.0006 borrow = 0.0894
        assert pnl == Decimal("0.0894")

    def test_negative_funding_paid(self):
        """SHORT perp 下若资金费率 < 0 则我方付出（funding_received 为负）。"""
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        meta = {
            "direction": "premium",
            "spot_size": "0.001", "perp_size": "0.001",
            "entry_spot_px": "50000", "entry_perp_px": "50100",
        }
        close = {
            "close_spot_px": "50050", "close_perp_px": "50050",
            "close_fees": "0.04",
            "funding_received": "-0.02",  # 付出
        }
        pnl = SpotPerpPaperSession._real_pnl_from_fills(meta, close)
        # 0.05 + 0.05 + (-0.02) - 0.04 = 0.04
        assert pnl == Decimal("0.04")

    def test_legacy_close_result_no_funding_fields(self):
        """D.2.a 旧 close_result 缺 funding_received/borrow_interest 字段时视为 0。"""
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        meta = {
            "direction": "premium",
            "spot_size": "0.001", "perp_size": "0.001",
            "entry_spot_px": "50000", "entry_perp_px": "50100",
        }
        close = {  # 没有 funding_received / borrow_interest
            "close_spot_px": "50050", "close_perp_px": "50050",
            "close_fees": "0.04",
        }
        pnl = SpotPerpPaperSession._real_pnl_from_fills(meta, close)
        # 等价于 D.2.a 测试: 0.05 + 0.05 - 0.04 = 0.06
        assert pnl == Decimal("0.06")


class TestFundingBorrowFetchers:
    """D.2.b — adapter 接口缺失/异常时静默返回 0。"""

    @pytest.mark.asyncio
    async def test_funding_no_perp_client_returns_zero(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        adapter = MagicMock()
        adapter._clients = {}
        out = await SpotPerpPaperSession._fetch_funding_received(
            adapter, "BTC/USDT", since_ms=1000,
        )
        assert out == Decimal("0")

    @pytest.mark.asyncio
    async def test_funding_aggregates_records(self):
        from app.exchanges.models import InstrumentType
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        client = MagicMock()
        client.fetch_funding_history = AsyncMock(return_value=[
            {"amount": "0.10"},
            {"amount": "0.05"},
            {"amount": "-0.03"},
        ])
        adapter = MagicMock()
        adapter._clients = {InstrumentType.PERPETUAL: client}
        out = await SpotPerpPaperSession._fetch_funding_received(
            adapter, "BTC/USDT", since_ms=1000,
        )
        assert out == Decimal("0.12")

    @pytest.mark.asyncio
    async def test_funding_exception_returns_zero(self):
        from app.exchanges.models import InstrumentType
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        client = MagicMock()
        client.fetch_funding_history = AsyncMock(side_effect=RuntimeError("api"))
        adapter = MagicMock()
        adapter._clients = {InstrumentType.PERPETUAL: client}
        out = await SpotPerpPaperSession._fetch_funding_received(
            adapter, "BTC/USDT", since_ms=1000,
        )
        assert out == Decimal("0")

    @pytest.mark.asyncio
    async def test_borrow_aggregates_and_converts(self):
        from app.exchanges.models import InstrumentType
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        client = MagicMock()
        # 0.0001 BTC 利息累计
        client.fetch_borrow_interest = AsyncMock(return_value=[
            {"interest": "0.00005"},
            {"interest": "0.00005"},
        ])
        adapter = MagicMock()
        adapter._clients = {InstrumentType.SPOT: client}
        out = await SpotPerpPaperSession._fetch_borrow_interest_usdt(
            adapter, "BTC/USDT", since_ms=1000,
            approx_price_usdt=Decimal("50000"),
        )
        # 0.0001 × 50000 = 5.0
        assert out == Decimal("5.0000")  # 0.0001 (sum) * 50000

    @pytest.mark.asyncio
    async def test_borrow_no_spot_client_returns_zero(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpPaperSession
        adapter = MagicMock()
        adapter._clients = {}
        out = await SpotPerpPaperSession._fetch_borrow_interest_usdt(
            adapter, "BTC/USDT", since_ms=1000,
            approx_price_usdt=Decimal("50000"),
        )
        assert out == Decimal("0")


# ---------------------------------------------------------------------------
# a — 基差扩大止损（_basis_widened_pct）
# ---------------------------------------------------------------------------


class TestBasisWidenedPct:
    """方向感知的基差扩大幅度计算（用于 stop_basis_widening_pct 触发判断）。"""

    def test_premium_widening_returns_positive_delta(self):
        # 入场 +0.30%，当前 +0.80% → 扩大 0.50pct
        out = SpotPerpPaperSession._basis_widened_pct(
            Decimal("0.30"), Decimal("0.80"),
        )
        assert out == Decimal("0.50")

    def test_premium_converging_returns_negative(self):
        # 入场 +0.30%，当前 +0.10% → 收敛（负值），不应触发止损
        out = SpotPerpPaperSession._basis_widened_pct(
            Decimal("0.30"), Decimal("0.10"),
        )
        assert out == Decimal("-0.20")

    def test_premium_crossing_zero_returns_negative(self):
        # 入场 +0.30%，越过 0 到 -0.20% → 大幅收敛/反转
        out = SpotPerpPaperSession._basis_widened_pct(
            Decimal("0.30"), Decimal("-0.20"),
        )
        assert out == Decimal("-0.50")

    def test_discount_widening_returns_positive_delta(self):
        # 入场 -0.30%，当前 -0.80%（更负）→ 扩大 0.50pct
        out = SpotPerpPaperSession._basis_widened_pct(
            Decimal("-0.30"), Decimal("-0.80"),
        )
        assert out == Decimal("0.50")

    def test_discount_converging_returns_negative(self):
        # 入场 -0.30%，当前 -0.10%（朝 0 走）→ 收敛
        out = SpotPerpPaperSession._basis_widened_pct(
            Decimal("-0.30"), Decimal("-0.10"),
        )
        assert out == Decimal("-0.20")

    def test_flat_entry_never_widens(self):
        # 入场基差为 0（边角，理论不会发生）→ 永远返回 0，不误触发
        out = SpotPerpPaperSession._basis_widened_pct(
            Decimal("0"), Decimal("0.50"),
        )
        assert out == Decimal("0")


# ---------------------------------------------------------------------------
# c — 方向独立入场阈值（entry_threshold_for）
# ---------------------------------------------------------------------------


class TestEntryThresholdFor:
    """per-direction 入场阈值：>0 时优先，0/未设回退到 entry_pct。"""

    def test_premium_uses_per_direction_when_set(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpStrategyConfig
        cfg = SpotPerpStrategyConfig(
            entry_pct=Decimal("0.30"),
            entry_pct_premium=Decimal("0.40"),
            entry_pct_discount=Decimal("0.60"),
        )
        assert cfg.entry_threshold_for("premium") == Decimal("0.40")

    def test_discount_uses_per_direction_when_set(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpStrategyConfig
        cfg = SpotPerpStrategyConfig(
            entry_pct=Decimal("0.30"),
            entry_pct_premium=Decimal("0.40"),
            entry_pct_discount=Decimal("0.60"),
        )
        assert cfg.entry_threshold_for("discount") == Decimal("0.60")

    def test_falls_back_to_entry_pct_when_per_direction_zero(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpStrategyConfig
        cfg = SpotPerpStrategyConfig(
            entry_pct=Decimal("0.30"),
            entry_pct_premium=Decimal("0"),
            entry_pct_discount=Decimal("0"),
        )
        assert cfg.entry_threshold_for("premium") == Decimal("0.30")
        assert cfg.entry_threshold_for("discount") == Decimal("0.30")

    def test_unknown_direction_falls_back(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpStrategyConfig
        cfg = SpotPerpStrategyConfig(
            entry_pct=Decimal("0.30"),
            entry_pct_premium=Decimal("0.40"),
            entry_pct_discount=Decimal("0.60"),
        )
        # 未知方向（理论不应出现，防御）退回 entry_pct
        assert cfg.entry_threshold_for("sideways") == Decimal("0.30")
        assert cfg.entry_threshold_for("") == Decimal("0.30")

    def test_case_insensitive(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpStrategyConfig
        cfg = SpotPerpStrategyConfig(
            entry_pct=Decimal("0.30"),
            entry_pct_discount=Decimal("0.60"),
        )
        assert cfg.entry_threshold_for("DISCOUNT") == Decimal("0.60")


# ---------------------------------------------------------------------------
# Config — yaml 加载与 override 合并新字段
# ---------------------------------------------------------------------------


class TestConfigYamlAndOverrides:
    def test_from_yaml_reads_new_fields(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpStrategyConfig
        cfg = SpotPerpStrategyConfig.from_yaml({
            "enabled": True,
            "entry": {
                "min_basis_pct": 0.30,
                "min_basis_pct_premium": 0.40,
                "min_basis_pct_discount": 0.60,
            },
            "exit": {
                "basis_convergence_pct": 0.10,
                "max_hold_hours": 12,
                "min_hold_minutes": 5,
                "stop_basis_widening_pct": 0.45,
            },
            "position": {
                "max_positions": 2, "size_usd": 50,
                "direction_filter": "both",
            },
        })
        assert cfg.entry_pct == Decimal("0.30")
        assert cfg.entry_pct_premium == Decimal("0.40")
        assert cfg.entry_pct_discount == Decimal("0.60")
        assert cfg.stop_basis_widening_pct == Decimal("0.45")

    def test_from_yaml_defaults_when_new_fields_missing(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpStrategyConfig
        # 旧 yaml 缺新字段 → entry_pct_* 默认 0（fallback）, stop_basis_widening_pct 默认 0.50
        cfg = SpotPerpStrategyConfig.from_yaml({
            "entry": {"min_basis_pct": 0.30},
            "exit": {"basis_convergence_pct": 0.10},
            "position": {"max_positions": 2, "size_usd": 50},
        })
        assert cfg.entry_pct_premium == Decimal("0")
        assert cfg.entry_pct_discount == Decimal("0")
        assert cfg.stop_basis_widening_pct == Decimal("0.50")

    def test_apply_overrides_updates_new_fields(self):
        from app.strategies.spot_perp_basis.paper_trading import SpotPerpStrategyConfig
        base = SpotPerpStrategyConfig()
        new = base.apply_overrides({
            "entry_pct_premium": "0.35",
            "entry_pct_discount": "0.55",
            "stop_basis_widening_pct": "0.40",
        })
        assert new.entry_pct_premium == Decimal("0.35")
        assert new.entry_pct_discount == Decimal("0.55")
        assert new.stop_basis_widening_pct == Decimal("0.40")
        # 其他字段不动
        assert new.entry_pct == base.entry_pct
        # 不可变：原对象未变
        assert base.entry_pct_premium == Decimal("0")


# ---------------------------------------------------------------------------
# b — 入场时机过滤（peak dropoff）
# ---------------------------------------------------------------------------


class TestPeakDropoff:
    """_check_peak_dropoff: 防接飞刀，要求 |basis| 已从峰值回落 ≥ M%。"""

    def _session(self, window_min: Decimal, dropoff: Decimal):
        runner = MagicMock()
        runner.latest_opportunities = []
        return SpotPerpPaperSession(
            runner=runner,
            strategy_config=SpotPerpStrategyConfig(
                peak_window_minutes=window_min,
                min_peak_dropoff_pct=dropoff,
            ),
        )

    def test_disabled_when_window_zero(self):
        s = self._session(Decimal("0"), Decimal("0.05"))
        ok, _ = s._check_peak_dropoff("BTC/USDT", Decimal("0.30"), 1_000_000)
        assert ok is True

    def test_disabled_when_dropoff_zero(self):
        s = self._session(Decimal("10"), Decimal("0"))
        ok, _ = s._check_peak_dropoff("BTC/USDT", Decimal("0.30"), 1_000_000)
        assert ok is True

    def test_empty_cache_returns_true_cold_start(self):
        s = self._session(Decimal("10"), Decimal("0.05"))
        ok, dropoff = s._check_peak_dropoff("BTC/USDT", Decimal("0.30"), 1_000_000)
        assert ok is True
        assert dropoff == Decimal("0")

    def test_basis_below_peak_meets_dropoff_passes(self):
        """峰值 0.50%，当前 0.30%，回落 0.20% ≥ 要求 0.05% → 入场通过。"""
        s = self._session(Decimal("10"), Decimal("0.05"))
        # 写入历史峰值
        now_ms = 1_000_000_000
        # 模拟 5 分钟前的 0.50% 峰值
        s._basis_peak_cache["BTC/USDT"] = [
            (now_ms - 5 * 60_000, Decimal("0.50")),
        ]
        ok, dropoff = s._check_peak_dropoff("BTC/USDT", Decimal("0.30"), now_ms)
        assert ok is True
        assert dropoff == Decimal("0.20")

    def test_basis_too_close_to_peak_fails(self):
        """峰值 0.32%，当前 0.30%，回落仅 0.02% < 要求 0.05% → 入场拒绝（接飞刀）。"""
        s = self._session(Decimal("10"), Decimal("0.05"))
        now_ms = 1_000_000_000
        s._basis_peak_cache["BTC/USDT"] = [
            (now_ms - 60_000, Decimal("0.32")),
        ]
        ok, dropoff = s._check_peak_dropoff("BTC/USDT", Decimal("0.30"), now_ms)
        assert ok is False
        assert dropoff == Decimal("0.02")

    def test_old_peak_outside_window_ignored(self):
        """峰值 0.80% 但已经 20min 前（超出 10min 窗口）→ 忽略，按空 cache 处理。"""
        s = self._session(Decimal("10"), Decimal("0.05"))
        now_ms = 1_000_000_000
        s._basis_peak_cache["BTC/USDT"] = [
            (now_ms - 20 * 60_000, Decimal("0.80")),  # 20min ago, expired
        ]
        ok, _ = s._check_peak_dropoff("BTC/USDT", Decimal("0.30"), now_ms)
        assert ok is True   # 空有效条目 → 通过（冷启动语义）

    def test_update_peak_cache_writes_and_evicts(self):
        s = self._session(Decimal("10"), Decimal("0.05"))
        now_ms = 1_000_000_000
        opp1 = SimpleNamespace(symbol="BTC/USDT", basis_pct=Decimal("0.30"))
        opp2 = SimpleNamespace(symbol="ETH/USDT", basis_pct=Decimal("-0.40"))
        s._update_peak_cache([opp1, opp2], now_ms)
        assert ("BTC/USDT" in s._basis_peak_cache)
        assert s._basis_peak_cache["BTC/USDT"][0][1] == Decimal("0.30")
        # 负值取 abs
        assert s._basis_peak_cache["ETH/USDT"][0][1] == Decimal("0.40")
        # 老条目（11min 前）会被淘汰
        s._basis_peak_cache["BTC/USDT"].insert(0, (now_ms - 11 * 60_000, Decimal("9")))
        s._update_peak_cache([opp1], now_ms + 60_000)
        # 11min ago 不在 10min window 内 → 应剔除
        for t, _ in s._basis_peak_cache["BTC/USDT"]:
            assert t > now_ms - 11 * 60_000
