"""
dgr_btc/engine.py — Martingale + Recenter + Stop Loss 纯逻辑引擎
==================================================================

设计原则（与 MARTINGALE_IMPL_PLAN_20260526.md 第一原则对齐）:
  - PURE LOGIC，无 IO
  - 同一份代码同时跑 LIVE (paper_trading.py) 和 backtest (dgr_engine.py)
  - 输入：price tick + 现有 state；输出：Decision (动作意图)
  - 状态更新由 caller 在 fill 回报后调用 apply_fill

W7 跨窗口验证最优参数（参见 MARTINGALE_RECENTER_VALIDATED_20260526.md）:
  grid_step=5%, factor=1.5, max_layers=5, tp_pct=5%, sl_pct=10%
  → W7 夏普 +1.05 / 跨 W1-W5 4/5 正夏普 / 5/5 胜 B&H

行为契约（不可破坏）:
  1. ENTRY layer 1 在 cycle 起始时无条件买入 weights[0]
  2. 每加一档后 recenter：next_buy = avg_cost × (1 - grid_step)
  3. 止损相对平均成本：low <= avg_cost × (1 - sl_pct) → SL_EXIT
  4. 止盈相对平均成本：price >= avg_cost × (1 + tp_pct) → TP_EXIT
  5. 加层使用 initial_cap × weights[i]（不是 cash × weights）—— 与 W7 回测一致
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import List, Optional


_ZERO = Decimal("0")
_ONE = Decimal("1")


# ─── Decision 类型 ───


class DecisionKind(str, Enum):
    NOOP = "NOOP"
    ADD_LAYER = "ADD_LAYER"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"


@dataclass(frozen=True)
class Decision:
    """Engine 输出的下一步动作意图（caller 负责执行）"""
    kind: DecisionKind
    target_price: Decimal  # 触发价（ADD_LAYER: next_buy; TP/SL: stop_price 或 price）
    stake_usdt: Decimal = _ZERO  # ADD_LAYER 用：金额（USDT）
    qty_btc: Decimal = _ZERO  # TP/SL 用：清仓数量
    reason: str = ""
    layer_index: int = -1  # ADD_LAYER 时本次要加第几层（0-indexed）


# ─── Layer & State ───


@dataclass(frozen=True)
class Layer:
    """一档已成交的仓位"""
    entry_price: Decimal  # 含 fee+slip 的实际成交价
    qty_btc: Decimal  # BTC 数量
    cost_usdt: Decimal  # 实际 USDT 流出（含 fee）


@dataclass
class StrategyState:
    """马丁策略状态机 —— 持久化字段全在这里"""
    cycle_id: int = 0  # 第几个 cycle（每次 TP/SL 清仓后 +1）
    layers: List[Layer] = field(default_factory=list)
    next_buy_price: Optional[Decimal] = None  # 下次加层触发价（avg_cost × (1-step)）
    n_tp: int = 0  # 累计止盈次数
    n_sl: int = 0  # 累计止损次数
    realized_pnl_usdt: Decimal = _ZERO  # 已实现 P&L（含费）

    # ── 计算属性 ──

    @property
    def total_qty(self) -> Decimal:
        return sum((l.qty_btc for l in self.layers), _ZERO)

    @property
    def total_cost(self) -> Decimal:
        return sum((l.cost_usdt for l in self.layers), _ZERO)

    @property
    def avg_cost(self) -> Decimal:
        """加权平均成本（USDT per BTC，已含成交时的 fee+slip）"""
        if not self.layers or self.total_qty == _ZERO:
            return _ZERO
        return self.total_cost / self.total_qty

    @property
    def n_layers(self) -> int:
        return len(self.layers)

    @property
    def is_in_cycle(self) -> bool:
        return self.n_layers > 0

    def mark_to_market(self, price: Decimal, fee_pct: Decimal) -> Decimal:
        """估算如果当前 close out 能拿回多少（含 sell fee）"""
        if not self.is_in_cycle:
            return _ZERO
        return self.total_qty * price * (_ONE - fee_pct)

    def unrealized_pnl(self, price: Decimal, fee_pct: Decimal) -> Decimal:
        """未实现 P&L = 当前清算价值 - 持仓总成本"""
        return self.mark_to_market(price, fee_pct) - self.total_cost


# ─── 配置 ───


@dataclass(frozen=True)
class EngineConfig:
    """策略参数（与 config.py:MartingaleConfig 子集一致）"""
    initial_capital: Decimal  # cycle 起始资金（每个 cycle 用同一个 initial_cap × weights[i]）
    grid_step: Decimal  # e.g. 0.05
    factor: Decimal  # e.g. 1.5
    max_layers: int  # e.g. 5
    tp_pct: Decimal  # e.g. 0.05
    sl_pct: Decimal  # e.g. 0.10
    layer_weights: List[Decimal]  # 归一化的层权重，len = max_layers
    fee_pct: Decimal  # 单边 taker fee + slippage（W7 验证用 0.0006）

    def __post_init__(self):
        if len(self.layer_weights) != self.max_layers:
            raise ValueError(
                f"layer_weights count {len(self.layer_weights)} != max_layers {self.max_layers}"
            )
        s = sum(self.layer_weights)
        if abs(s - _ONE) > Decimal("0.001"):
            raise ValueError(f"layer_weights sum to {s}, expected 1.0")
        if self.sl_pct <= self.tp_pct:
            raise ValueError(
                f"sl_pct ({self.sl_pct}) must be > tp_pct ({self.tp_pct})"
            )


# ─── Engine ───


class MartingaleEngine:
    """马丁 + 再定心 + 止损纯逻辑引擎

    用法:
        engine = MartingaleEngine(config)
        state = StrategyState()  # 或从 storage load

        # 每个 tick:
        decision = engine.decide(state, price=Decimal("75000"), low=Decimal("74900"))
        if decision.kind != DecisionKind.NOOP:
            fill = broker.execute(decision)  # 调外部 broker
            engine.apply_fill(state, decision, fill)
    """

    def __init__(self, config: EngineConfig):
        self.cfg = config

    # ─── 主决策 ───

    def decide(
        self,
        state: StrategyState,
        price: Decimal,
        low: Decimal,
        allow_add_layer: bool = True,
    ) -> Decision:
        """根据当前 tick 价格 + state 决定下一步动作

        优先级:
          1. 如果没仓位 → ENTRY layer 1（受 allow_add_layer gate 控制）
          2. 检查 STOP_LOSS (用 low，最高优先级，永远启用)
          3. 检查 ADD_LAYER (用 low，加 1 档；多档由 caller 在同一 tick 内连续调 decide)
             — 受 allow_add_layer gate 控制
          4. 检查 TAKE_PROFIT (用 price，永远启用)
          5. NOOP

        Args:
            allow_add_layer: §6.2 #8 regime detector gate.
                False = 暂停所有加仓（ENTRY + ADD_LAYER），但 SL/TP 仍正常工作。
                paper 和 backtest 都需传同样的 gate 值才能保持 mirror byte-equal。
        """
        cfg = self.cfg

        # 1. cycle 启动：买入第 1 层（受 gate）
        if not state.is_in_cycle:
            if not allow_add_layer:
                return Decision(
                    kind=DecisionKind.NOOP,
                    target_price=price,
                    reason="REGIME_GATE_BLOCKS_ENTRY",
                )
            stake = cfg.initial_capital * cfg.layer_weights[0]
            return Decision(
                kind=DecisionKind.ADD_LAYER,
                target_price=price,  # 市价买入
                stake_usdt=stake,
                reason=f"CYCLE_{state.cycle_id}_ENTRY_LAYER_0",
                layer_index=0,
            )

        avg = state.avg_cost
        total_qty = state.total_qty

        # 2. STOP LOSS（intrabar low 触发，永远启用）
        stop_price = avg * (_ONE - cfg.sl_pct)
        if low <= stop_price:
            return Decision(
                kind=DecisionKind.STOP_LOSS,
                target_price=stop_price,
                qty_btc=total_qty,
                reason=f"CYCLE_{state.cycle_id}_SL@{stop_price:.2f}_avg={avg:.2f}",
            )

        # 3. ADD_LAYER（intrabar low 触达 next_buy，受 gate）
        if (
            allow_add_layer
            and state.n_layers < cfg.max_layers
            and state.next_buy_price is not None
            and low <= state.next_buy_price
        ):
            stake = cfg.initial_capital * cfg.layer_weights[state.n_layers]
            return Decision(
                kind=DecisionKind.ADD_LAYER,
                target_price=state.next_buy_price,
                stake_usdt=stake,
                reason=f"CYCLE_{state.cycle_id}_LAYER_{state.n_layers}@{state.next_buy_price:.2f}",
                layer_index=state.n_layers,
            )

        # 4. TAKE PROFIT（用 close price，永远启用）
        tp_trigger = avg * (_ONE + cfg.tp_pct)
        if price >= tp_trigger:
            return Decision(
                kind=DecisionKind.TAKE_PROFIT,
                target_price=price,
                qty_btc=total_qty,
                reason=f"CYCLE_{state.cycle_id}_TP@{price:.2f}_avg={avg:.2f}",
            )

        # 5. NOOP
        return Decision(kind=DecisionKind.NOOP, target_price=price)

    # ─── State 转移 ───

    def apply_fill(
        self,
        state: StrategyState,
        decision: Decision,
        fill_price: Decimal,
        fill_qty: Decimal,
        fill_cost: Decimal,
    ) -> None:
        """根据 fill 回报更新 state（in-place mutation）

        Args:
            state: 当前状态机
            decision: 触发本次 fill 的决策
            fill_price: 实际成交价（含 fee+slip 的 effective price）
            fill_qty: BTC 成交量
            fill_cost: USDT 实际流出/流入（含 fee）
        """
        if decision.kind == DecisionKind.ADD_LAYER:
            state.layers.append(Layer(
                entry_price=fill_price,
                qty_btc=fill_qty,
                cost_usdt=fill_cost,
            ))
            # 再定心：下次买入价 = 当前 avg_cost × (1 - grid_step)
            state.next_buy_price = state.avg_cost * (_ONE - self.cfg.grid_step)

        elif decision.kind == DecisionKind.TAKE_PROFIT:
            # proceeds（净流入 USDT）= fill_qty × fill_price - fee 已含在 fill_cost
            # fill_cost 在 SELL 语义下是"净流入"（正数），所以 realized = proceeds - total_cost
            realized = fill_cost - state.total_cost
            state.realized_pnl_usdt += realized
            state.n_tp += 1
            self._close_cycle(state)

        elif decision.kind == DecisionKind.STOP_LOSS:
            realized = fill_cost - state.total_cost
            state.realized_pnl_usdt += realized
            state.n_sl += 1
            self._close_cycle(state)

    def _close_cycle(self, state: StrategyState) -> None:
        """关闭当前 cycle，准备开新 cycle"""
        state.layers = []
        state.next_buy_price = None
        state.cycle_id += 1

    # ─── 辅助计算（供 caller 推断 fill 应该是什么样的） ───

    def compute_fill_cost_buy(self, stake_usdt: Decimal) -> tuple[Decimal, Decimal]:
        """已知 USDT stake，计算 buy 后实际拿到多少 BTC + 实际 cost

        return: (fill_qty_btc, fill_cost_usdt)
        Note: fill_cost_usdt == stake_usdt（市价 buy 用全额 USDT，fee 摊在数量上）
        """
        # 实际 effective price = mark_price × (1 + fee_pct)
        # 但 caller 知道 mark price 后才能精确算，这里只算到 stake 维度
        return (_ZERO, stake_usdt)

    def compute_fill_qty_buy(
        self, stake_usdt: Decimal, mark_price: Decimal
    ) -> tuple[Decimal, Decimal]:
        """已知 mark price，计算 buy 后 (effective_price, qty_btc)

        与 W7 回测脚本完全一致:
            fill = mark_price × (1 + fee_pct)
            qty = stake / fill
        """
        fill = mark_price * (_ONE + self.cfg.fee_pct)
        qty = stake_usdt / fill if fill > 0 else _ZERO
        return (fill, qty)

    def compute_proceeds_sell(
        self, total_qty: Decimal, mark_price: Decimal
    ) -> Decimal:
        """卖出 total_qty 在 mark_price 上能拿回多少 USDT（含 sell fee）

        与 W7 回测脚本完全一致:
            fill = mark_price × (1 - fee_pct)
            proceeds = qty × fill
        """
        fill = mark_price * (_ONE - self.cfg.fee_pct)
        return total_qty * fill
