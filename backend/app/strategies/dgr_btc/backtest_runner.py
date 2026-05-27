"""
dgr_btc/backtest_runner.py — Martingale 策略回测驱动器
==========================================================

设计原则 (与 MARTINGALE_IMPL_PLAN_20260526.md "回测=LIVE 镜像"对齐):
  - 不包含任何策略逻辑 — 全部委托给 engine.MartingaleEngine
  - 仅做 IO 层: csv → bar iterator → 调 engine.decide → 模拟 fill → 调 engine.apply_fill
  - LIVE 模式有对应的 paper_trading.py，使用同一 engine

驱动流程（每个 bar）:
  1. while engine.decide(...) 返回非 NOOP:
       if ADD_LAYER: 计算 fill_qty + apply_fill (用 stake → btc)
       if TP/SL: 计算 proceeds + apply_fill (用 btc → usdt) + break
  2. mark-to-market: equity = cash + state.mark_to_market(price, fee)

P5 验收门:
  - W7: 总收益 +26.77% ± 0.5% (sl_pct=10% 跨窗口验证配置)
  - W1-W5: 跨窗口数字与 /tmp/W7_strategies/martingale_recenter_multi.py 一致
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

import pandas as pd

from app.strategies.dgr_btc.engine import (
    Decision,
    DecisionKind,
    EngineConfig,
    MartingaleEngine,
    StrategyState,
)


_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass
class EquityPoint:
    ts: datetime
    equity_usdt: Decimal
    cash: Decimal
    position_value: Decimal
    n_layers: int


@dataclass
class BacktestResult:
    initial_capital: Decimal
    final_equity: Decimal
    total_return_pct: Decimal
    n_tp: int
    n_sl: int
    n_cycles: int
    max_layer_hit: int
    max_drawdown_pct: Decimal
    equity_curve: List[EquityPoint] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "initial_capital": str(self.initial_capital),
            "final_equity": str(self.final_equity),
            "total_return_pct": str(self.total_return_pct),
            "n_tp": self.n_tp,
            "n_sl": self.n_sl,
            "n_cycles": self.n_cycles,
            "max_layer_hit": self.max_layer_hit,
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "n_equity_points": len(self.equity_curve),
        }


class MartingaleBacktestRunner:
    """K 线 bar 驱动 MartingaleEngine 的最小回测器。

    Usage:
        cfg = EngineConfig(...)
        runner = MartingaleBacktestRunner(cfg)
        result = runner.run(df)  # df: timestamp/open/high/low/close[/volume]
        print(result.summary())
    """

    def __init__(self, config: EngineConfig):
        self.engine = MartingaleEngine(config)
        self.cfg = config

    def run(
        self,
        df: pd.DataFrame,
        init_state: Optional[StrategyState] = None,
        init_cash: Optional[Decimal] = None,
    ) -> BacktestResult:
        """跑回测，返回最终结果与权益曲线。

        Args:
            df: DataFrame with columns: timestamp, open, high, low, close
                timestamp 可为 string 或 datetime；自动转换
            init_state: 起始 strategy state. None → 全新 fresh state (默认).
                       传入则续跑 (P3 修复: mirror_check 续昨日场景).
            init_cash:  起始 cash. None → cfg.initial_capital.
        """
        df = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)

        state = init_state if init_state is not None else StrategyState()
        cash = init_cash if init_cash is not None else self.cfg.initial_capital
        equity_curve: List[EquityPoint] = []
        max_layer_hit = 0

        for _, row in df.iterrows():
            ts = row["timestamp"].to_pydatetime() if hasattr(row["timestamp"], "to_pydatetime") else row["timestamp"]
            price = Decimal(str(row["close"]))
            low = Decimal(str(row["low"]))

            # process all decisions in this bar (engine.decide is single-step, loop to fixed point)
            for _ in range(self.cfg.max_layers + 3):  # safety bound: never more than max_layers+ENTRY+SL/TP
                d = self.engine.decide(state, price, low)
                if d.kind == DecisionKind.NOOP:
                    break
                if d.kind == DecisionKind.ADD_LAYER:
                    cash, _ = self._handle_add_layer(state, d, cash)
                    if state.n_layers > max_layer_hit:
                        max_layer_hit = state.n_layers
                    # continue loop to potentially add more layers if low still triggers
                elif d.kind == DecisionKind.TAKE_PROFIT:
                    cash = self._handle_exit(state, d, cash)
                    break  # one exit per bar
                elif d.kind == DecisionKind.STOP_LOSS:
                    cash = self._handle_exit(state, d, cash)
                    break

            # mark-to-market equity
            position_value = state.mark_to_market(price, self.cfg.fee_pct)
            equity = cash + position_value
            equity_curve.append(EquityPoint(
                ts=ts,
                equity_usdt=equity,
                cash=cash,
                position_value=position_value,
                n_layers=state.n_layers,
            ))

        # ─── compute metrics ───
        if not equity_curve:
            raise ValueError("Empty equity curve - no bars processed")

        final = equity_curve[-1].equity_usdt
        total_ret_pct = (final / self.cfg.initial_capital - _ONE) * Decimal("100")

        # max drawdown
        peak = self.cfg.initial_capital
        max_dd = _ZERO
        for pt in equity_curve:
            if pt.equity_usdt > peak:
                peak = pt.equity_usdt
            if peak > 0:
                dd = (pt.equity_usdt - peak) / peak
                if dd < max_dd:
                    max_dd = dd
        max_dd_pct = max_dd * Decimal("100")

        return BacktestResult(
            initial_capital=self.cfg.initial_capital,
            final_equity=final,
            total_return_pct=total_ret_pct,
            n_tp=state.n_tp,
            n_sl=state.n_sl,
            n_cycles=state.cycle_id,
            max_layer_hit=max_layer_hit,
            max_drawdown_pct=max_dd_pct,
            equity_curve=equity_curve,
        )

    # ─── 内部 fill 模拟（与 W7 参考脚本完全一致） ───

    def _handle_add_layer(self, state, decision: Decision, cash: Decimal) -> tuple[Decimal, Decimal]:
        """ADD_LAYER fill: 计算 effective price + btc qty, 扣 cash, apply_fill.

        Returns (new_cash, fill_qty)
        """
        stake = decision.stake_usdt
        if cash < stake:
            stake = cash
        if stake <= _ZERO:
            return cash, _ZERO

        fill_px, qty = self.engine.compute_fill_qty_buy(stake, decision.target_price)
        new_cash = cash - stake
        self.engine.apply_fill(state, decision, fill_px, qty, stake)
        return new_cash, qty

    def _handle_exit(self, state, decision: Decision, cash: Decimal) -> Decimal:
        """TP/SL fill: 计算 proceeds, 加 cash, apply_fill."""
        proceeds = self.engine.compute_proceeds_sell(state.total_qty, decision.target_price)
        new_cash = cash + proceeds
        self.engine.apply_fill(state, decision, decision.target_price, state.total_qty, proceeds)
        return new_cash
