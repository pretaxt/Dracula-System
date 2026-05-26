"""Tests for dgr_btc.strategy_core — on_tick / paired_inverse / recenter / trend halt."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.strategy_core import DgrBtcStrategy
from app.strategies.dgr_btc.types import (
    MarketState,
    MarketType,
    Side,
    Trade,
)


def _market(price: Decimal, ts: datetime, funding_settled: bool = False) -> MarketState:
    return MarketState(
        timestamp=ts,
        spot_price=price,
        perp_price=price,
        funding_rate=Decimal("0.0001"),
        bid_depth_usdt=Decimal("1000000"),
        ask_depth_usdt=Decimal("1000000"),
        realized_vol_1h=Decimal("0.5"),
        funding_settled=funding_settled,
    )


def _trade(market: MarketType, side: Side, price: Decimal, qty: Decimal, ts: datetime) -> Trade:
    return Trade(
        trade_id="T1",
        order_id="O1",
        symbol="BTC/USDT",
        market=market,
        side=side,
        price=price,
        quantity=qty,
        fee=price * qty * Decimal("0.0002"),
        is_maker=True,
        timestamp=ts,
    )


def test_init_requires_start_price_when_center_zero():
    cfg = DgrBtcStrategyConfig()
    assert cfg.grid_center_price == 0
    with pytest.raises(ValueError):
        DgrBtcStrategy(cfg, start_price=None)


def test_init_with_start_price():
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    assert strat.grid.lower_bound == Decimal("68000")
    assert strat.grid.upper_bound == Decimal("92000")
    assert strat.spot_pos.quantity == cfg.spot_initial_btc
    assert strat.perp_pos.quantity == -cfg.short_initial_btc


def test_first_tick_no_trigger():
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    intents = strat.on_tick(_market(Decimal("80000"), ts))
    assert intents == []


def test_up_cross_emits_sell_pair():
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    strat.on_tick(_market(Decimal("80000"), ts))  # 初始化 last_price
    intents = strat.on_tick(_market(Decimal("80300"), ts + timedelta(seconds=30)))
    # 上穿 80250: spot SELL + perp SELL (加空)
    assert len(intents) == 2
    sides = {i.side for i in intents}
    markets = {i.market for i in intents}
    assert sides == {Side.SELL}
    assert markets == {MarketType.SPOT, MarketType.PERP}


def test_down_cross_emits_buy_pair():
    """下穿 1 个 grid (80000 → 79800 跨过 79750) → 1 BUY pair = 2 intents."""
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    strat.on_tick(_market(Decimal("80000"), ts))
    # 80000→79800: idx 48 → idx 47, 跨 1 个 grid (79750)
    intents = strat.on_tick(_market(Decimal("79800"), ts + timedelta(seconds=30)))
    assert len(intents) == 2
    sides = {i.side for i in intents}
    markets = {i.market for i in intents}
    assert sides == {Side.BUY}
    assert markets == {MarketType.SPOT, MarketType.PERP}
    assert intents[0].price == Decimal("79750.00")


def test_trend_filter_halts_after_5_consecutive():
    """⭐ 核心: 连续 5 个上穿 grid → on_tick return [] 不开新 pair."""
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    strat.on_tick(_market(Decimal("80000"), ts))
    # 连续 5 个上穿 (每次跨 1 grid)
    prices = [Decimal("80300"), Decimal("80550"), Decimal("80800"),
              Decimal("81050"), Decimal("81300")]
    last_intents = None
    for i, p in enumerate(prices, start=1):
        last_intents = strat.on_tick(_market(p, ts + timedelta(seconds=30 * i)))
        # 前 4 个应该有 intents（计数 < 5）, 第 5 个触发 trend filter
        if i < 5:
            assert len(last_intents) >= 1, f"step {i} should emit intents"
    # 第 6 次上穿在 trend filter 内
    last_intents = strat.on_tick(_market(Decimal("81550"), ts + timedelta(seconds=300)))
    assert last_intents == []
    assert strat.grid.consecutive_direction_grids >= 5


def test_recenter_triggered_at_12pct():
    """price 偏离 center >= 12% → maybe_recenter 触发 rebuild_around."""
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    strat.on_tick(_market(Decimal("80000"), ts))
    # 涨到 89800: 80000 * 1.1225 = 89800 (偏离 12.25% > 12%)
    new_ts = ts + timedelta(seconds=30)
    strat.on_tick(_market(Decimal("89800"), new_ts))
    assert strat.n_recenters >= 1
    # strategy.center 同步到 new_center = 89800
    assert strat.center == Decimal("89800")


def test_recenter_respects_cooldown():
    """600s cooldown 内不触发第二次 recenter."""
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    strat.on_tick(_market(Decimal("80000"), ts))
    # 第 1 次 recenter
    strat.on_tick(_market(Decimal("89800"), ts + timedelta(seconds=30)))
    assert strat.n_recenters == 1
    # 100s 后又偏离 12%, 但 cooldown < 600s → 不 recenter
    far_price = strat.center * Decimal("1.13")
    strat.on_tick(_market(far_price, ts + timedelta(seconds=130)))
    assert strat.n_recenters == 1  # 仍然 1


def test_recenter_after_cooldown_triggers_again():
    """cooldown 过后 + 偏离 12% → 触发第 2 次 recenter."""
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    strat.on_tick(_market(Decimal("80000"), ts))
    strat.on_tick(_market(Decimal("89800"), ts + timedelta(seconds=30)))
    # 700s 后再 12% 偏离
    far_price = strat.center * Decimal("1.13")
    strat.on_tick(_market(far_price, ts + timedelta(seconds=730)))
    assert strat.n_recenters == 2


def test_on_trade_updates_position_and_cash():
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    initial_cash = strat.cash
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # spot SELL 0.002 @ 80250
    trade = _trade(MarketType.SPOT, Side.SELL, Decimal("80250"), Decimal("0.002"), ts)
    strat.on_trade(trade)
    assert strat.spot_pos.quantity == cfg.spot_initial_btc - Decimal("0.002")
    # cash 增加 (price * qty - fee)
    assert strat.cash > initial_cash
    assert strat.n_trades == 1


def test_apply_funding_short_receives_positive():
    cfg = DgrBtcStrategyConfig()
    strat = DgrBtcStrategy(cfg, start_price=Decimal("80000"))
    initial_cash = strat.cash
    market = _market(Decimal("80000"), datetime(2026, 1, 1, tzinfo=timezone.utc))
    # short qty = 0.05; rate = +0.0001 → 收 0.05 * 80000 * 0.0001 = $0.40
    payment = strat.apply_funding(market)
    assert payment > 0
    assert strat.cash == initial_cash + payment
    assert strat.funding_paid == payment
