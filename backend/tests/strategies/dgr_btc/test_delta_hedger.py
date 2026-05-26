"""Tests for dgr_btc.delta_hedger — Delta limits + capacity caps."""
from __future__ import annotations

from decimal import Decimal

from app.strategies.dgr_btc.delta_hedger import DeltaHedger, HedgeAction
from app.strategies.dgr_btc.types import MarketType, Position


def _make_spot(qty: Decimal) -> Position:
    return Position(
        symbol="BTC/USDT",
        market=MarketType.SPOT,
        quantity=qty,
        avg_entry=Decimal("80000"),
    )


def _make_perp_short(qty: Decimal) -> Position:
    """qty > 0 → short qty BTC (stored as negative)."""
    return Position(
        symbol="BTC/USDT:USDT",
        market=MarketType.PERP,
        quantity=-qty,
        avg_entry=Decimal("80000"),
    )


def test_delta_calc_neutral():
    h = DeltaHedger(
        upper_limit=Decimal("0.02"),
        lower_limit=Decimal("-0.02"),
        max_spot=Decimal("0.1"),
        max_short=Decimal("0.1"),
    )
    state = h.calc_state(_make_spot(Decimal("0.05")), _make_perp_short(Decimal("0.05")))
    assert state.delta == Decimal("0")
    assert state.within_limits is True


def test_sell_pair_skip_on_delta_lower_limit():
    """delta=-0.018 已接近下限 -0.02; sell pair 操作 Δ -0.04 → 触限 skip."""
    h = DeltaHedger(
        upper_limit=Decimal("0.02"),
        lower_limit=Decimal("-0.02"),
        max_spot=Decimal("0.1"),
        max_short=Decimal("0.1"),
    )
    # spot=0.041 short=0.059 → delta=-0.018
    spot = _make_spot(Decimal("0.041"))
    perp = _make_perp_short(Decimal("0.059"))
    action = h.evaluate_sell_pair(spot, perp, qty=Decimal("0.002"))
    # new delta = (0.041-0.002) - (0.059+0.002) = 0.039 - 0.061 = -0.022 < -0.02
    assert action == HedgeAction.SKIP_SELL


def test_buy_pair_skip_on_delta_upper_limit():
    """delta=+0.018 接近上限; buy pair 操作 Δ +0.04 → 触限 skip."""
    h = DeltaHedger(
        upper_limit=Decimal("0.02"),
        lower_limit=Decimal("-0.02"),
        max_spot=Decimal("0.1"),
        max_short=Decimal("0.1"),
    )
    spot = _make_spot(Decimal("0.059"))
    perp = _make_perp_short(Decimal("0.041"))
    action = h.evaluate_buy_pair(spot, perp, qty=Decimal("0.002"))
    # new delta = (0.059+0.002) - (0.041-0.002) = 0.061 - 0.039 = +0.022 > +0.02
    assert action == HedgeAction.SKIP_BUY


def test_sell_pair_reject_max_short():
    """short 已到 max_short, 再加 short 超限 → REJECT_LIMIT."""
    h = DeltaHedger(
        upper_limit=Decimal("0.5"),  # 放大 delta 避免被 delta 拦
        lower_limit=Decimal("-0.5"),
        max_spot=Decimal("0.5"),
        max_short=Decimal("0.1"),
    )
    spot = _make_spot(Decimal("0.2"))
    perp = _make_perp_short(Decimal("0.1"))
    action = h.evaluate_sell_pair(spot, perp, qty=Decimal("0.002"))
    assert action == HedgeAction.REJECT_LIMIT


def test_buy_pair_reject_max_spot():
    """spot 已到 max_spot, 再 buy → REJECT_LIMIT."""
    h = DeltaHedger(
        upper_limit=Decimal("0.5"),
        lower_limit=Decimal("-0.5"),
        max_spot=Decimal("0.1"),
        max_short=Decimal("0.5"),
    )
    spot = _make_spot(Decimal("0.1"))
    perp = _make_perp_short(Decimal("0.2"))
    action = h.evaluate_buy_pair(spot, perp, qty=Decimal("0.002"))
    assert action == HedgeAction.REJECT_LIMIT


def test_normal_pairs_approved():
    """正常状态 sell + buy 都 APPROVE."""
    h = DeltaHedger(
        upper_limit=Decimal("0.02"),
        lower_limit=Decimal("-0.02"),
        max_spot=Decimal("0.1"),
        max_short=Decimal("0.1"),
    )
    spot = _make_spot(Decimal("0.05"))
    perp = _make_perp_short(Decimal("0.05"))
    assert h.evaluate_sell_pair(spot, perp, Decimal("0.002")) == HedgeAction.APPROVE
    assert h.evaluate_buy_pair(spot, perp, Decimal("0.002")) == HedgeAction.APPROVE
