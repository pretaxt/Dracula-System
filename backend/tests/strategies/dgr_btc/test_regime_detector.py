"""§6.2 #8 regime detector 单测.

测试 compute_regime_metrics 在不同 market regime 下的判定:
  - 低波 + 浅回撤 = bad (sideways grinder)
  - 高波 = normal (即便回撤浅)
  - 深熊 = normal (vol 不一定低但 dd 深)
  - warmup 期 = 总返回 normal (避免冷启动误判)
"""
from __future__ import annotations

import math

import pytest

from app.strategies.dgr_btc.regime_detector import (
    aggregate_hourly_to_daily,
    closes_from_klines,
    compute_regime_metrics,
)


def test_empty_returns_normal():
    """空输入返回 normal regime (不触发 gate)"""
    m = compute_regime_metrics([])
    assert m.is_bad is False
    assert m.sample_size == 0


def test_single_close_returns_normal():
    m = compute_regime_metrics([50000.0])
    assert m.is_bad is False


def test_low_vol_shallow_dd_triggers_bad():
    """30d 数据: 平稳震荡 ±0.5%/天, max DD -8% → vol 低 + dd 浅 → bad"""
    # 构造 30d closes: 从 50000 缓慢小幅震荡到 48000 (跌 4%)
    base = 50000.0
    closes = []
    for i in range(30):
        # 微幅震荡 ±0.5%, 整体趋势 -4%
        drift = -base * 0.04 * (i / 30)
        wiggle = base * 0.003 * (1 if i % 2 == 0 else -1)
        closes.append(base + drift + wiggle)
    m = compute_regime_metrics(closes, vol_threshold=0.25, dd_threshold=-0.15)
    # vol 应该 < 25% 年化, dd 在 -4% 左右 > -15% → bad
    assert m.realized_vol_annualized < 0.25, f"实际 vol={m.realized_vol_annualized}"
    assert m.max_drawdown_pct > -0.15, f"实际 dd={m.max_drawdown_pct}"
    assert m.is_bad is True


def test_high_vol_triggers_normal():
    """30d 高波 (±5%/天) 应判定 normal (vol > 阈值)"""
    base = 50000.0
    closes = []
    for i in range(30):
        # 大幅震荡 ±5%
        closes.append(base * (1 + 0.05 * math.sin(i * 0.7)))
    m = compute_regime_metrics(closes)
    assert m.realized_vol_annualized > 0.25, f"实际 vol={m.realized_vol_annualized}"
    assert m.is_bad is False  # 高波不算 grinder


def test_deep_drawdown_triggers_normal():
    """30d 深熊 -40% 应判定 normal (dd 超过阈值, 不算 grinder)"""
    closes = [50000.0 * (1 - 0.40 * i / 29) for i in range(30)]  # 线性跌到 30000
    m = compute_regime_metrics(closes, vol_threshold=0.25, dd_threshold=-0.15)
    assert m.max_drawdown_pct < -0.15  # dd 跌穿阈值
    assert m.is_bad is False  # 深熊不是 grinder


def test_warmup_period_returns_normal():
    """数据不足 warmup_days 时 (默认 20) 不触发, 即便指标看起来 bad"""
    # 只 10 天数据 + 低波 + 浅 dd
    base = 50000.0
    closes = [base * (1 + 0.001 * i) for i in range(10)]
    m = compute_regime_metrics(closes, warmup_days=20)
    assert m.sample_size == 10
    assert m.is_bad is False  # warmup 保护


def test_zero_volatility_constant_price_returns_normal():
    """完全静止 (vol=0, dd=0) 边界: 严格按规则 vol=0<阈值 且 dd=0>阈值 → bad"""
    closes = [50000.0] * 30
    m = compute_regime_metrics(closes, vol_threshold=0.25, dd_threshold=-0.15)
    assert m.realized_vol_annualized == 0.0
    assert m.max_drawdown_pct == 0.0
    # vol 0 < 0.25 AND dd 0 > -0.15 → is_bad True (极端 grinder = 死水)
    assert m.is_bad is True


def test_aggregate_hourly_to_daily_basic():
    """24 个 1h close 聚合成 1 个 daily close (取最后一根)"""
    hourly = [100.0 + i for i in range(24)]  # 100..123
    daily = aggregate_hourly_to_daily(hourly, bars_per_day=24)
    assert daily == [123.0]


def test_aggregate_hourly_with_remainder():
    """26 个 1h: 1 日 + 余 2 根, 应输出 [day1_close, last]"""
    hourly = [100.0 + i for i in range(26)]
    daily = aggregate_hourly_to_daily(hourly, bars_per_day=24)
    # 24-th index = 123 (day 1 完整), 然后 +余 2 根的最后 = 125
    assert daily == [123.0, 125.0]


def test_closes_from_klines_object_attrs():
    """kline obj with .close attr"""
    class K:
        def __init__(self, c):
            self.close = c
    closes = closes_from_klines([K(100.0), K(101.5), K(102.7)])
    assert closes == [100.0, 101.5, 102.7]


def test_closes_from_klines_dict_fallback():
    """dict 也能取 (defensive)"""
    closes = closes_from_klines([{"close": 100.0}, {"close": 101.5}])
    assert closes == [100.0, 101.5]


def test_engine_decide_respects_gate_no_entry():
    """allow_add_layer=False 时: 空仓不开新 cycle (gate blocks entry)"""
    from decimal import Decimal

    from app.strategies.dgr_btc.engine import (
        DecisionKind, EngineConfig, MartingaleEngine, StrategyState,
    )

    cfg = EngineConfig(
        initial_capital=Decimal("10000"),
        grid_step=Decimal("0.05"), factor=Decimal("1.5"), max_layers=4,
        tp_pct=Decimal("0.04"), sl_pct=Decimal("0.10"),
        layer_weights=[
            Decimal("0.12308"), Decimal("0.18462"),
            Decimal("0.27692"), Decimal("0.41538"),
        ],
        fee_pct=Decimal("0.0006"),
    )
    engine = MartingaleEngine(cfg)
    state = StrategyState()  # 空仓

    # gate ON (默认 allow=True): 应触发 ENTRY
    d_allow = engine.decide(state, Decimal("75000"), Decimal("74900"), allow_add_layer=True)
    assert d_allow.kind == DecisionKind.ADD_LAYER

    # gate OFF (allow=False): 应 NOOP, reason 标记 REGIME_GATE
    d_block = engine.decide(state, Decimal("75000"), Decimal("74900"), allow_add_layer=False)
    assert d_block.kind == DecisionKind.NOOP
    assert "REGIME_GATE" in (d_block.reason or "")


def test_engine_decide_respects_gate_blocks_add_layer():
    """有持仓时 allow=False 拦 ADD_LAYER 但 SL/TP 仍工作"""
    from decimal import Decimal

    from app.strategies.dgr_btc.engine import (
        DecisionKind, EngineConfig, Layer, MartingaleEngine, StrategyState,
    )

    cfg = EngineConfig(
        initial_capital=Decimal("10000"),
        grid_step=Decimal("0.05"), factor=Decimal("1.5"), max_layers=4,
        tp_pct=Decimal("0.04"), sl_pct=Decimal("0.10"),
        layer_weights=[
            Decimal("0.12308"), Decimal("0.18462"),
            Decimal("0.27692"), Decimal("0.41538"),
        ],
        fee_pct=Decimal("0.0006"),
    )
    engine = MartingaleEngine(cfg)
    state = StrategyState(
        cycle_id=0,
        layers=[Layer(entry_price=Decimal("75000"), qty_btc=Decimal("0.016"), cost_usdt=Decimal("1200"))],
        next_buy_price=Decimal("71250"),  # avg × 0.95
    )

    # 价格触达 next_buy + gate OFF: 不加层 (NOOP)
    d = engine.decide(state, Decimal("71300"), Decimal("71200"), allow_add_layer=False)
    assert d.kind == DecisionKind.NOOP

    # 价格触达 SL + gate OFF: 仍触发 SL (SL 不受 gate 控制)
    sl_low = Decimal("75000") * Decimal("0.89")  # 跌穿 -10%
    d_sl = engine.decide(state, Decimal("70000"), sl_low, allow_add_layer=False)
    assert d_sl.kind == DecisionKind.STOP_LOSS

    # 价格触达 TP + gate OFF: 仍触发 TP
    d_tp = engine.decide(state, Decimal("78001"), Decimal("77900"), allow_add_layer=False)
    assert d_tp.kind == DecisionKind.TAKE_PROFIT
