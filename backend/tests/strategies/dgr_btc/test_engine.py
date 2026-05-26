"""Unit tests for dgr_btc.engine — Martingale + Recenter + SL pure logic"""
from decimal import Decimal

import pytest

from app.strategies.dgr_btc.engine import (
    Decision,
    DecisionKind,
    EngineConfig,
    Layer,
    MartingaleEngine,
    StrategyState,
)


# ─── 测试配置（与 W7 跨窗口验证最优一致） ───

WEIGHTS = [
    Decimal("0.0760"),
    Decimal("0.1141"),
    Decimal("0.1711"),
    Decimal("0.2566"),
    Decimal("0.3822"),  # 调整最后一个让总和精确到 1.0
]


@pytest.fixture
def cfg():
    """W7 跨窗口验证最优配置"""
    return EngineConfig(
        initial_capital=Decimal("10000"),
        grid_step=Decimal("0.05"),
        factor=Decimal("1.5"),
        max_layers=5,
        tp_pct=Decimal("0.05"),
        sl_pct=Decimal("0.10"),
        layer_weights=WEIGHTS,
        fee_pct=Decimal("0.0006"),
    )


@pytest.fixture
def engine(cfg):
    return MartingaleEngine(cfg)


@pytest.fixture
def state():
    return StrategyState()


# ─── A. Cycle 启动 ───


def test_empty_state_decides_layer_0(engine, state):
    """没仓位时第一个 tick 应该买 layer 0"""
    decision = engine.decide(state, price=Decimal("75000"), low=Decimal("74900"))
    assert decision.kind == DecisionKind.ADD_LAYER
    assert decision.layer_index == 0
    assert decision.stake_usdt == Decimal("10000") * WEIGHTS[0]  # = 760
    assert "ENTRY_LAYER_0" in decision.reason


def test_apply_fill_after_layer_0_sets_next_buy(engine, cfg, state):
    """买入 layer 0 后 next_buy 应为 avg_cost × (1 - grid_step)"""
    decision = engine.decide(state, Decimal("75000"), Decimal("74900"))
    fill_price = Decimal("75000") * (Decimal("1") + cfg.fee_pct)
    qty = decision.stake_usdt / fill_price
    engine.apply_fill(state, decision, fill_price, qty, decision.stake_usdt)

    assert state.n_layers == 1
    assert state.is_in_cycle
    # avg_cost should equal fill_price up to Decimal precision tolerance
    # (reconstruction from qty has tiny rounding)
    assert abs(state.avg_cost - fill_price) < Decimal("0.0001")
    # next_buy = avg × 0.95
    expected_next_buy = state.avg_cost * Decimal("0.95")
    assert state.next_buy_price == expected_next_buy


# ─── B. 加层（再定心） ───


def test_add_layer_2_when_price_drops_below_next_buy(engine, cfg, state):
    """价格 low 触达 next_buy 时应该加 layer 2"""
    # 先填 layer 0
    d0 = engine.decide(state, Decimal("100000"), Decimal("100000"))
    fill_px = Decimal("100000") * (Decimal("1") + cfg.fee_pct)
    qty0 = d0.stake_usdt / fill_px
    engine.apply_fill(state, d0, fill_px, qty0, d0.stake_usdt)

    next_buy = state.next_buy_price  # ≈ 95057
    # 价格收 96000, low 触达 95000 (< next_buy)
    d1 = engine.decide(state, price=Decimal("96000"), low=Decimal("95000"))
    assert d1.kind == DecisionKind.ADD_LAYER
    assert d1.layer_index == 1
    assert d1.stake_usdt == Decimal("10000") * WEIGHTS[1]


def test_recenter_lowers_next_buy_after_layer_2(engine, cfg, state):
    """加完 layer 2 后 next_buy 应基于新 avg_cost"""
    # layer 0
    d0 = engine.decide(state, Decimal("100000"), Decimal("100000"))
    px0 = Decimal("100000") * (Decimal("1") + cfg.fee_pct)
    qty0 = d0.stake_usdt / px0
    engine.apply_fill(state, d0, px0, qty0, d0.stake_usdt)

    # layer 1 在 next_buy ≈ 95057 处
    next_buy_1 = state.next_buy_price
    d1 = engine.decide(state, price=next_buy_1, low=next_buy_1)
    px1 = next_buy_1 * (Decimal("1") + cfg.fee_pct)
    qty1 = d1.stake_usdt / px1
    engine.apply_fill(state, d1, px1, qty1, d1.stake_usdt)

    # avg_cost 应该被拉低
    assert state.avg_cost < px0
    # next_buy = new avg × 0.95
    assert state.next_buy_price == state.avg_cost * Decimal("0.95")
    assert state.next_buy_price < next_buy_1  # 比上一个 next_buy 低


def test_max_layers_caps_at_5(engine, cfg, state):
    """连续触达 next_buy 加到 5 层后不再加"""
    # 第 1 档 @ 100000
    px = Decimal("100000")
    d = engine.decide(state, px, px)
    fill = px * (Decimal("1") + cfg.fee_pct)
    qty = d.stake_usdt / fill
    engine.apply_fill(state, d, fill, qty, d.stake_usdt)

    # 模拟价格一路跌，每次 low 触达 next_buy
    for expected_layer in range(1, 5):
        nb = state.next_buy_price
        d = engine.decide(state, price=nb, low=nb)
        assert d.kind == DecisionKind.ADD_LAYER
        assert d.layer_index == expected_layer
        fill = nb * (Decimal("1") + cfg.fee_pct)
        qty = d.stake_usdt / fill
        engine.apply_fill(state, d, fill, qty, d.stake_usdt)

    # 现在 5 层满，再跌也不加
    assert state.n_layers == 5
    # 强制 low 远低于 next_buy_price（即便它还是个有效值）
    very_low = state.avg_cost * Decimal("0.5")
    d = engine.decide(state, price=very_low, low=very_low)
    # 不应该是 ADD_LAYER（应该是 STOP_LOSS 因为远低于 sl 阈值）
    assert d.kind != DecisionKind.ADD_LAYER


# ─── C. Take Profit ───


def test_tp_triggers_when_price_above_avg_plus_tp_pct(engine, cfg, state):
    """price 突破 avg × (1 + tp_pct) 应该触发 TP"""
    # 买 layer 0 @ 100000
    px = Decimal("100000")
    d = engine.decide(state, px, px)
    fill = px * (Decimal("1") + cfg.fee_pct)
    qty = d.stake_usdt / fill
    engine.apply_fill(state, d, fill, qty, d.stake_usdt)

    # 价格涨到 avg × 1.06 > tp 阈值 (avg × 1.05)
    tp_target = state.avg_cost * Decimal("1.06")
    d_tp = engine.decide(state, price=tp_target, low=tp_target)
    assert d_tp.kind == DecisionKind.TAKE_PROFIT
    assert d_tp.qty_btc == state.total_qty
    assert "TP" in d_tp.reason


def test_apply_fill_tp_closes_cycle(engine, cfg, state):
    """TP fill 应该清空 layers + cycle_id+1 + n_tp+1"""
    px = Decimal("100000")
    d = engine.decide(state, px, px)
    fill = px * (Decimal("1") + cfg.fee_pct)
    qty = d.stake_usdt / fill
    engine.apply_fill(state, d, fill, qty, d.stake_usdt)

    # TP
    tp_price = state.avg_cost * Decimal("1.06")
    d_tp = engine.decide(state, tp_price, tp_price)
    proceeds = engine.compute_proceeds_sell(state.total_qty, tp_price)
    engine.apply_fill(state, d_tp, tp_price, state.total_qty, proceeds)

    assert not state.is_in_cycle
    assert state.cycle_id == 1
    assert state.n_tp == 1
    assert state.n_sl == 0
    assert state.realized_pnl_usdt > 0  # 涨了 6% TP，净盈利


# ─── D. Stop Loss ───


def test_sl_triggers_when_low_below_avg_minus_sl_pct(engine, cfg, state):
    """low 跌破 avg × (1 - sl_pct) 应该触发 SL"""
    px = Decimal("100000")
    d = engine.decide(state, px, px)
    fill = px * (Decimal("1") + cfg.fee_pct)
    qty = d.stake_usdt / fill
    engine.apply_fill(state, d, fill, qty, d.stake_usdt)

    # low 跌到 avg × 0.85 < sl 阈值 (avg × 0.90)
    sl_low = state.avg_cost * Decimal("0.85")
    d_sl = engine.decide(state, price=sl_low, low=sl_low)
    assert d_sl.kind == DecisionKind.STOP_LOSS
    assert "SL" in d_sl.reason


def test_sl_priority_over_add_layer(engine, cfg, state):
    """SL 应该比 ADD_LAYER 优先 —— 同一 tick 同时触发 next_buy 和 sl 时走 SL"""
    px = Decimal("100000")
    d = engine.decide(state, px, px)
    fill = px * (Decimal("1") + cfg.fee_pct)
    qty = d.stake_usdt / fill
    engine.apply_fill(state, d, fill, qty, d.stake_usdt)

    # 同一 tick low 既触达 next_buy (avg × 0.95) 又触达 sl (avg × 0.90)
    # → 优先 SL（保护资金）
    sl_price = state.avg_cost * Decimal("0.85")
    d_next = engine.decide(state, price=sl_price, low=sl_price)
    assert d_next.kind == DecisionKind.STOP_LOSS


def test_apply_fill_sl_closes_cycle_with_loss(engine, cfg, state):
    """SL fill 应该 cycle_id+1 + n_sl+1 + realized < 0"""
    px = Decimal("100000")
    d = engine.decide(state, px, px)
    fill = px * (Decimal("1") + cfg.fee_pct)
    qty = d.stake_usdt / fill
    engine.apply_fill(state, d, fill, qty, d.stake_usdt)

    sl_price = state.avg_cost * (Decimal("1") - cfg.sl_pct)
    d_sl = engine.decide(state, sl_price, sl_price)
    proceeds = engine.compute_proceeds_sell(state.total_qty, sl_price)
    engine.apply_fill(state, d_sl, sl_price, state.total_qty, proceeds)

    assert not state.is_in_cycle
    assert state.cycle_id == 1
    assert state.n_sl == 1
    assert state.n_tp == 0
    assert state.realized_pnl_usdt < 0  # 跌了 10%+fee SL，必亏


# ─── E. NOOP / 边界 ───


def test_noop_when_price_inside_band(engine, cfg, state):
    """价格在 [sl, tp] 区间内 + low > next_buy → NOOP"""
    px = Decimal("100000")
    d = engine.decide(state, px, px)
    fill = px * (Decimal("1") + cfg.fee_pct)
    qty = d.stake_usdt / fill
    engine.apply_fill(state, d, fill, qty, d.stake_usdt)

    # 价格在 avg × 0.97（高于 next_buy 0.95，低于 tp 1.05）
    inside_price = state.avg_cost * Decimal("0.97")
    d_noop = engine.decide(state, price=inside_price, low=inside_price)
    assert d_noop.kind == DecisionKind.NOOP


def test_cycle_id_increments_after_tp(engine, cfg, state):
    """完整 cycle: buy → TP → cycle_id=1"""
    # cycle 0
    px = Decimal("100000")
    d0 = engine.decide(state, px, px)
    fill0 = px * (Decimal("1") + cfg.fee_pct)
    qty0 = d0.stake_usdt / fill0
    engine.apply_fill(state, d0, fill0, qty0, d0.stake_usdt)

    tp_px = state.avg_cost * Decimal("1.06")
    d_tp = engine.decide(state, tp_px, tp_px)
    proceeds = engine.compute_proceeds_sell(state.total_qty, tp_px)
    engine.apply_fill(state, d_tp, tp_px, state.total_qty, proceeds)

    assert state.cycle_id == 1

    # 下个 tick 应该开 cycle 1 的 layer 0
    d_next = engine.decide(state, tp_px, tp_px)
    assert d_next.kind == DecisionKind.ADD_LAYER
    assert d_next.layer_index == 0
    assert f"CYCLE_1_ENTRY" in d_next.reason


# ─── F. 配置校验 ───


def test_invalid_config_layer_weights_count():
    with pytest.raises(ValueError, match="layer_weights count"):
        EngineConfig(
            initial_capital=Decimal("10000"),
            grid_step=Decimal("0.05"),
            factor=Decimal("1.5"),
            max_layers=5,
            tp_pct=Decimal("0.05"),
            sl_pct=Decimal("0.10"),
            layer_weights=[Decimal("0.5"), Decimal("0.5")],  # 只有 2 个，不是 5
            fee_pct=Decimal("0.0006"),
        )


def test_invalid_config_sl_le_tp():
    with pytest.raises(ValueError, match="sl_pct .* must be > tp_pct"):
        EngineConfig(
            initial_capital=Decimal("10000"),
            grid_step=Decimal("0.05"),
            factor=Decimal("1.5"),
            max_layers=5,
            tp_pct=Decimal("0.10"),  # tp > sl
            sl_pct=Decimal("0.05"),
            layer_weights=WEIGHTS,
            fee_pct=Decimal("0.0006"),
        )


def test_invalid_config_weights_dont_sum_to_one():
    bad_weights = [Decimal("0.1")] * 5  # 总和 0.5
    with pytest.raises(ValueError, match="layer_weights sum"):
        EngineConfig(
            initial_capital=Decimal("10000"),
            grid_step=Decimal("0.05"),
            factor=Decimal("1.5"),
            max_layers=5,
            tp_pct=Decimal("0.05"),
            sl_pct=Decimal("0.10"),
            layer_weights=bad_weights,
            fee_pct=Decimal("0.0006"),
        )


# ─── G. 工具函数 ───


def test_compute_fill_qty_buy_matches_w7_formula(engine, cfg):
    """compute_fill_qty_buy 必须复现 W7 回测脚本的 fill 计算"""
    stake = Decimal("760")
    mark = Decimal("100000")
    eff, qty = engine.compute_fill_qty_buy(stake, mark)
    # eff = mark × (1 + fee) = 100000 × 1.0006 = 100060
    assert eff == Decimal("100060")
    # qty = stake / eff
    expected_qty = stake / Decimal("100060")
    assert abs(qty - expected_qty) < Decimal("0.0000001")


def test_compute_proceeds_sell_matches_w7_formula(engine, cfg):
    """compute_proceeds_sell 必须复现 W7 回测脚本"""
    qty = Decimal("0.01")
    mark = Decimal("100000")
    proceeds = engine.compute_proceeds_sell(qty, mark)
    # fill = mark × (1 - fee) = 99940
    # proceeds = qty × fill = 0.01 × 99940 = 999.4
    assert proceeds == Decimal("999.4")


def test_mark_to_market_returns_zero_when_no_position(engine, cfg, state):
    assert state.mark_to_market(Decimal("100000"), cfg.fee_pct) == Decimal("0")


def test_unrealized_pnl_negative_when_price_below_avg(engine, cfg, state):
    px = Decimal("100000")
    d = engine.decide(state, px, px)
    fill = px * (Decimal("1") + cfg.fee_pct)
    qty = d.stake_usdt / fill
    engine.apply_fill(state, d, fill, qty, d.stake_usdt)

    # 价格跌 3%
    down_px = px * Decimal("0.97")
    upnl = state.unrealized_pnl(down_px, cfg.fee_pct)
    assert upnl < 0
