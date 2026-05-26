"""P5 验收: dgr_btc/backtest_runner 跑 W7/W1-W5 输出与 /tmp/W7_strategies 参考脚本一致

ground truth (from /tmp/W7_strategies/martingale_recenter_multi.py output, 2026-05-26):
    config: grid=5% / factor=1.5 / max_layers=5 / TP=5% / SL=10%

    W7 (2025-01 → 2026-05):  +26.77% Sharpe +1.05 MaxDD -16.88% TP=39 SL=5
    W1 LUNA  (2022-04~06):   -25.23% Sharpe -3.33 MaxDD -32.64% TP=7 SL=4
    W2 FTX   (2022-10~12):   +1.88%  Sharpe +0.50 MaxDD -14.59% TP=5 SL=1
    W3 2022bear (2022-01~12): -5.10% Sharpe +0.05 MaxDD -32.68% TP=37 SL=9
    W4 2023 chop (2023-04~09): +11.62% Sharpe +1.36 MaxDD -10.91% TP=7 SL=1
    W5 2024 drop (2024-03~05): -0.73% Sharpe +0.08 MaxDD -11.15% TP=6 SL=1

容差: 总收益 ±0.5pp / TP/SL 计数 ±1 / MaxDD ±1pp
"""
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from app.strategies.dgr_btc.backtest_runner import MartingaleBacktestRunner
from app.strategies.dgr_btc.engine import EngineConfig


# 跨窗口验证最优配置 (sl=10%)
WEIGHTS = [
    Decimal("0.0760"),
    Decimal("0.1141"),
    Decimal("0.1711"),
    Decimal("0.2566"),
    Decimal("0.3822"),
]


def _canonical_config() -> EngineConfig:
    """W1-W5 跨窗口验证最优 (sl=10%)"""
    return EngineConfig(
        initial_capital=Decimal("10000"),
        grid_step=Decimal("0.05"),
        factor=Decimal("1.5"),
        max_layers=5,
        tp_pct=Decimal("0.05"),
        sl_pct=Decimal("0.10"),
        layer_weights=WEIGHTS,
        fee_pct=Decimal("0.0006"),  # 0.04% taker + 0.02% slippage
    )


# 数据路径：Desktop/Dracula/backtest_windows/ (用户工作目录)
WINDOWS_DIR = Path("/Users/YANG/Desktop/Dracula/backtest_windows")


def _load_window(filename: str) -> pd.DataFrame:
    path = WINDOWS_DIR / filename
    if not path.exists():
        pytest.skip(f"window CSV not found: {path}")
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def _within(actual: Decimal, expected: Decimal, tol: Decimal) -> bool:
    return abs(actual - expected) <= tol


# ─── 参考数字 (ground truth from Python script) ───

REFERENCE = {
    "W7": {
        "file": "W7_full_cycle_25_26_1h.csv",
        "ret_pct": Decimal("26.77"),
        "max_dd_pct": Decimal("-16.88"),
        "n_tp": 39,
        "n_sl": 5,
    },
    "W1": {
        "file": "W1_luna_crash_1h.csv",
        "ret_pct": Decimal("-25.23"),
        "max_dd_pct": Decimal("-32.64"),
        "n_tp": 7,
        "n_sl": 4,
    },
    "W2": {
        "file": "W2_ftx_crash_1h.csv",
        "ret_pct": Decimal("1.88"),
        "max_dd_pct": Decimal("-14.59"),
        "n_tp": 5,
        "n_sl": 1,
    },
    "W3": {
        "file": "W3_2022_bear_1h.csv",
        "ret_pct": Decimal("-5.10"),
        "max_dd_pct": Decimal("-32.68"),
        "n_tp": 37,
        "n_sl": 9,
    },
    "W4": {
        "file": "W4_2023_sideways_1h.csv",
        "ret_pct": Decimal("11.62"),
        "max_dd_pct": Decimal("-10.91"),
        "n_tp": 7,
        "n_sl": 1,
    },
    "W5": {
        "file": "W5_2024_spring_drop_1h.csv",
        "ret_pct": Decimal("-0.73"),
        "max_dd_pct": Decimal("-11.15"),
        "n_tp": 6,
        "n_sl": 1,
    },
}


# 容差
RET_TOL = Decimal("0.5")  # ±0.5pp
DD_TOL = Decimal("1.0")   # ±1pp
COUNT_TOL = 1


def _run_window(window_key: str) -> dict:
    ref = REFERENCE[window_key]
    df = _load_window(ref["file"])
    cfg = _canonical_config()
    runner = MartingaleBacktestRunner(cfg)
    result = runner.run(df)
    return {
        "ret_pct": result.total_return_pct,
        "max_dd_pct": result.max_drawdown_pct,
        "n_tp": result.n_tp,
        "n_sl": result.n_sl,
        "ref": ref,
    }


@pytest.mark.parametrize("window", ["W7", "W1", "W2", "W3", "W4", "W5"])
def test_window_total_return_within_tolerance(window):
    """每个窗口总收益必须在 ±0.5pp 内"""
    res = _run_window(window)
    actual = res["ret_pct"]
    expected = res["ref"]["ret_pct"]
    msg = (
        f"[{window}] return {actual:.2f}% (expected {expected:.2f}% ± {RET_TOL}pp)\n"
        f"  ref TP={res['ref']['n_tp']} SL={res['ref']['n_sl']}\n"
        f"  got TP={res['n_tp']} SL={res['n_sl']} MaxDD={res['max_dd_pct']:.2f}%"
    )
    assert _within(actual, expected, RET_TOL), msg


@pytest.mark.parametrize("window", ["W7", "W1", "W2", "W3", "W4", "W5"])
def test_window_tp_count_within_tolerance(window):
    """TP 触发次数必须 ±1"""
    res = _run_window(window)
    actual = res["n_tp"]
    expected = res["ref"]["n_tp"]
    assert abs(actual - expected) <= COUNT_TOL, (
        f"[{window}] n_tp={actual} expected {expected} ± {COUNT_TOL}"
    )


@pytest.mark.parametrize("window", ["W7", "W1", "W2", "W3", "W4", "W5"])
def test_window_sl_count_within_tolerance(window):
    """SL 触发次数必须 ±1"""
    res = _run_window(window)
    actual = res["n_sl"]
    expected = res["ref"]["n_sl"]
    assert abs(actual - expected) <= COUNT_TOL, (
        f"[{window}] n_sl={actual} expected {expected} ± {COUNT_TOL}"
    )


@pytest.mark.parametrize("window", ["W7", "W1", "W2", "W3", "W4", "W5"])
def test_window_max_drawdown_within_tolerance(window):
    """Max DD ±1pp"""
    res = _run_window(window)
    actual = res["max_dd_pct"]
    expected = res["ref"]["max_dd_pct"]
    assert _within(actual, expected, DD_TOL), (
        f"[{window}] MaxDD {actual:.2f}% expected {expected:.2f}% ± {DD_TOL}pp"
    )


# 单独 W7 的"金标"测试（最严苛容差）
def test_w7_gold_standard_strict():
    """W7 是训练窗口，容差更严 (±0.3pp / ±0 trades)"""
    res = _run_window("W7")
    ref = res["ref"]

    # 总收益严格
    assert _within(res["ret_pct"], ref["ret_pct"], Decimal("0.3")), (
        f"W7 gold: return {res['ret_pct']:.4f}% vs ref {ref['ret_pct']:.2f}% (need ±0.3pp)"
    )
    # 交易数完全一致
    assert res["n_tp"] == ref["n_tp"], f"W7 gold: n_tp={res['n_tp']} vs ref {ref['n_tp']}"
    assert res["n_sl"] == ref["n_sl"], f"W7 gold: n_sl={res['n_sl']} vs ref {ref['n_sl']}"
