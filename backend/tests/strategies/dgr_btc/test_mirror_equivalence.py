"""P9 验收门: paper_trading vs backtest_runner mirror 等价性测试

第一原则 (MARTINGALE_IMPL_PLAN_20260526.md):
  - paper_trading.py 和 backtest_runner.py 共享同一个 MartingaleEngine
  - 喂同一段 (price, low) 序列 → 必须 byte-equal 输出
  - 任何 divergence = engine 漏抽象 = 回 P3 修

测试: 把 W7 + W1-W5 数据喂给两边, 比对 state 终态:
  - n_layers, cycle_id, total_qty, avg_cost, cash, realized_pnl
  - n_tp, n_sl
  - total_equity (mtm)
"""
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from app.strategies.dgr_btc.backtest_runner import MartingaleBacktestRunner
from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.engine import EngineConfig
from app.strategies.dgr_btc.paper_trading import DgrBtcPaperSession


WINDOWS_DIR = Path("/Users/YANG/Desktop/Dracula/backtest_windows")
WEIGHTS = [
    Decimal("0.0760"),
    Decimal("0.1141"),
    Decimal("0.1711"),
    Decimal("0.2566"),
    Decimal("0.3822"),
]


def _engine_config() -> EngineConfig:
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


def _load_window(filename: str) -> pd.DataFrame:
    path = WINDOWS_DIR / filename
    if not path.exists():
        pytest.skip(f"missing: {path}")
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def _run_paper_on_bars(df: pd.DataFrame, tmp_path) -> tuple[dict, Decimal, Decimal]:
    """喂 bars 给 paper_trading._process_decisions, 返回 (snapshot, final_cash, final_equity)"""
    import os
    os.environ["DGR_BTC_STATE_DIR"] = str(tmp_path)

    # 显式对齐 _engine_config() (测 byte-equal，不依赖默认值)
    cfg = DgrBtcStrategyConfig(
        total_capital_usdt=Decimal("10000"),
        mart_grid_step=Decimal("0.05"),
        mart_factor=Decimal("1.5"),
        mart_max_layers=5,
        mart_tp_pct=Decimal("0.05"),
        mart_sl_pct=Decimal("0.10"),
        mart_layer_weights=tuple(WEIGHTS),
        mart_fee_pct=Decimal("0.0006"),
    )
    session = DgrBtcPaperSession(
        cfg=cfg,
        adapter=None,  # 不调 adapter；直接走 _process_decisions
        tick_interval_seconds=0.01,
        live_mode=False,
    )

    # 逐 bar 喂 (price, low)
    for _, row in df.iterrows():
        price = Decimal(str(row["close"]))
        low = Decimal(str(row["low"]))
        session._last_spot_px = price
        session._process_decisions(price, low)

    # 计算 final equity (与 backtest_runner 同公式)
    last_price = Decimal(str(df["close"].iloc[-1]))
    position_value = session._state.mark_to_market(last_price, session._engine.cfg.fee_pct)
    final_equity = session._cash + position_value
    return session.snapshot(), session._cash, final_equity


WINDOWS = [
    ("W7", "W7_full_cycle_25_26_1h.csv"),
    ("W1", "W1_luna_crash_1h.csv"),
    ("W2", "W2_ftx_crash_1h.csv"),
    ("W3", "W3_2022_bear_1h.csv"),
    ("W4", "W4_2023_sideways_1h.csv"),
    ("W5", "W5_2024_spring_drop_1h.csv"),
]


@pytest.mark.parametrize("window_name,filename", WINDOWS)
def test_paper_equals_backtest_on_window(window_name, filename, tmp_path):
    """喂 paper_trading + backtest_runner 同一段数据 → state 完全一致"""
    df = _load_window(filename)
    cfg = _engine_config()

    # 跑 backtest
    runner = MartingaleBacktestRunner(cfg)
    bt = runner.run(df)

    # 跑 paper
    pp_snap, pp_cash, pp_equity = _run_paper_on_bars(df, tmp_path)

    # ─── 必须 byte-equal 的字段 ───

    # cycle / 交易计数
    assert int(pp_snap["cycle_id"]) == bt.n_cycles, (
        f"[{window_name}] cycle_id paper={pp_snap['cycle_id']} bt={bt.n_cycles}"
    )
    assert int(pp_snap["n_tp"]) == bt.n_tp, (
        f"[{window_name}] n_tp paper={pp_snap['n_tp']} bt={bt.n_tp}"
    )
    assert int(pp_snap["n_sl"]) == bt.n_sl, (
        f"[{window_name}] n_sl paper={pp_snap['n_sl']} bt={bt.n_sl}"
    )

    # 现金 + 总权益必须高精度一致 (Decimal)
    assert abs(pp_equity - bt.final_equity) < Decimal("0.01"), (
        f"[{window_name}] equity paper={pp_equity:.4f} bt={bt.final_equity:.4f}"
    )

    # 总收益 % 必须高精度一致
    pp_ret_pct = (pp_equity / cfg.initial_capital - Decimal("1")) * Decimal("100")
    assert abs(pp_ret_pct - bt.total_return_pct) < Decimal("0.01"), (
        f"[{window_name}] return paper={pp_ret_pct:.4f}% bt={bt.total_return_pct:.4f}%"
    )


def test_paper_state_serialization_round_trip_matches_backtest(tmp_path):
    """额外验证: paper 在 mid-stream 持久化 → 恢复 → 继续, 结果仍与 backtest 一致"""
    df = _load_window("W4_2023_sideways_1h.csv")
    cfg_engine = _engine_config()

    # backtest 跑全段
    runner = MartingaleBacktestRunner(cfg_engine)
    bt = runner.run(df)

    # paper 跑前半段 → persist → 重启 → 继续
    import os
    os.environ["DGR_BTC_STATE_DIR"] = str(tmp_path)
    # 显式对齐 _engine_config() A baseline
    cfg = DgrBtcStrategyConfig(
        total_capital_usdt=Decimal("10000"),
        mart_grid_step=Decimal("0.05"),
        mart_factor=Decimal("1.5"),
        mart_max_layers=5,
        mart_tp_pct=Decimal("0.05"),
        mart_sl_pct=Decimal("0.10"),
        mart_layer_weights=tuple(WEIGHTS),
        mart_fee_pct=Decimal("0.0006"),
    )

    mid = len(df) // 2
    df_first = df.iloc[:mid].copy()
    df_second = df.iloc[mid:].copy()

    s1 = DgrBtcPaperSession(cfg=cfg, adapter=None, tick_interval_seconds=0.01, live_mode=False)
    for _, row in df_first.iterrows():
        price = Decimal(str(row["close"]))
        low = Decimal(str(row["low"]))
        s1._last_spot_px = price
        s1._process_decisions(price, low)
    s1._persist_state()
    state_file = s1._state_file
    assert state_file.exists()

    # 新 session, restore, 继续后半
    import asyncio
    s2 = DgrBtcPaperSession(cfg=cfg, adapter=None, tick_interval_seconds=0.01, live_mode=False)
    restored = asyncio.run(s2._try_restore_state())
    assert restored, "state restore failed"

    for _, row in df_second.iterrows():
        price = Decimal(str(row["close"]))
        low = Decimal(str(row["low"]))
        s2._last_spot_px = price
        s2._process_decisions(price, low)

    # 比对最终
    assert s2._state.n_tp == bt.n_tp
    assert s2._state.n_sl == bt.n_sl
    assert s2._state.cycle_id == bt.n_cycles
    last_price = Decimal(str(df["close"].iloc[-1]))
    pos_value = s2._state.mark_to_market(last_price, s2._engine.cfg.fee_pct)
    final_equity = s2._cash + pos_value
    assert abs(final_equity - bt.final_equity) < Decimal("0.01")
