"""
dgr_btc/atomic_pair.py
======================
AtomicPairExecutor — 双腿原子性执行 (Phase E.1)。

核心契约: paired_inverse 策略每次 grid trigger 同时下 spot + perp 对偶单。
**禁止单腿持仓**: 若一条腿成交、另一条未成交 N 秒后, 自动反向 taker 平掉已成交腿。

三模式:
  - PAPER (默认): 双腿同时 mock fill, 可注入 single_leg_failure_rate 用于测试
  - DRY_RUN (Phase E live=true + dry_run=true): 走完整 broker 调用但不真发单
  - LIVE (Phase E live=true + dry_run=false): 真实 broker 下单 + wait fill + unwind

Reference: 全局 memory feedback_no_single_leg.md
  "事前预检 + unwind 兜底 + 后台 reconciliation 三层防护"

设计决策 (已与用户对齐):
  - unwind_timeout_sec: 5s (一条腿成交后等 5s 另一条未成 → unwind)
  - unwind_order_type: MARKET (taker, 保证立即平掉, 不再单腿挂着)
  - paper 模式 single_leg_failure_rate 默认 0, env 注入测试用
"""
from __future__ import annotations

import asyncio
import os
import random as _random
import uuid as _uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

import structlog

from app.strategies.dgr_btc.types import (
    MarketState,
    MarketType,
    OrderType,
    Side,
    Trade,
)
from app.strategies.dgr_btc.strategy_core import OrderIntent

logger = structlog.get_logger(__name__)


class PairExecutionMode(str, Enum):
    PAPER = "PAPER"
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


class PairOutcome(str, Enum):
    BOTH_FILLED = "BOTH_FILLED"
    SPOT_ONLY_UNWOUND = "SPOT_ONLY_UNWOUND"
    PERP_ONLY_UNWOUND = "PERP_ONLY_UNWOUND"
    BOTH_FAILED = "BOTH_FAILED"
    PRETRADE_REJECTED = "PRETRADE_REJECTED"


@dataclass
class PairResult:
    outcome: PairOutcome
    trades: list[Trade] = field(default_factory=list)
    unwind_trade: Optional[Trade] = None
    reject_reason: Optional[str] = None
    elapsed_ms: int = 0


class AtomicPairExecutor:
    """每次 grid trigger 把 spot + perp 配对执行, 保证双腿原子性 (single-leg-free)."""

    def __init__(
        self,
        mode: PairExecutionMode = PairExecutionMode.PAPER,
        unwind_timeout_sec: float = 5.0,
        spot_fee_rate: Decimal = Decimal("0.001"),
        perp_maker_fee: Decimal = Decimal("0.0002"),
        perp_taker_fee: Decimal = Decimal("0.0005"),
        single_leg_failure_rate: float = 0.0,
        broker_adapter: Any | None = None,
        pretrade_checker: Any | None = None,
    ) -> None:
        self.mode = mode
        self.unwind_timeout = unwind_timeout_sec
        self.spot_fee = spot_fee_rate
        self.perp_maker_fee = perp_maker_fee
        self.perp_taker_fee = perp_taker_fee
        self.single_leg_failure_rate = single_leg_failure_rate
        self.broker = broker_adapter
        self.pretrade = pretrade_checker
        self.n_pairs_total = 0
        self.n_both_filled = 0
        self.n_single_leg_unwound = 0
        self.n_pretrade_rejected = 0

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    async def execute_pair(
        self,
        spot_intent: OrderIntent,
        perp_intent: OrderIntent,
        market: MarketState,
    ) -> PairResult:
        """处理一对 spot + perp intent (一定要成对, 调用方拆 list)."""
        import time as _t
        t0 = _t.monotonic()
        self.n_pairs_total += 1

        # 1. 事前预检
        if self.pretrade is not None:
            ok, reason = self.pretrade.validate(spot_intent, perp_intent, market)
            if not ok:
                self.n_pretrade_rejected += 1
                logger.warning(
                    "dgr_btc_atomic_pretrade_rejected",
                    reason=reason,
                    spot=spot_intent.reason,
                    perp=perp_intent.reason,
                )
                return PairResult(
                    outcome=PairOutcome.PRETRADE_REJECTED,
                    reject_reason=reason,
                    elapsed_ms=int((_t.monotonic() - t0) * 1000),
                )

        # 2. 模式分派
        if self.mode == PairExecutionMode.PAPER:
            result = await self._execute_paper(spot_intent, perp_intent, market)
        elif self.mode == PairExecutionMode.DRY_RUN:
            result = await self._execute_dry_run(spot_intent, perp_intent, market)
        else:  # LIVE
            result = await self._execute_live(spot_intent, perp_intent, market)

        result.elapsed_ms = int((_t.monotonic() - t0) * 1000)

        # 3. 统计
        if result.outcome == PairOutcome.BOTH_FILLED:
            self.n_both_filled += 1
        elif result.outcome in (
            PairOutcome.SPOT_ONLY_UNWOUND,
            PairOutcome.PERP_ONLY_UNWOUND,
        ):
            self.n_single_leg_unwound += 1
            logger.warning(
                "dgr_btc_atomic_unwind_triggered",
                outcome=result.outcome.value,
                elapsed_ms=result.elapsed_ms,
            )
        return result

    # ------------------------------------------------------------------
    # PAPER 模式: 默认双腿成, 可注入 single_leg_failure_rate 模拟单腿失败
    # ------------------------------------------------------------------

    async def _execute_paper(
        self,
        spot_intent: OrderIntent,
        perp_intent: OrderIntent,
        market: MarketState,
    ) -> PairResult:
        spot_ok = _random.random() >= self.single_leg_failure_rate
        perp_ok = _random.random() >= self.single_leg_failure_rate

        trades: list[Trade] = []
        if spot_ok:
            trades.append(self._mock_fill(spot_intent, market, is_taker=False))
        if perp_ok:
            trades.append(self._mock_fill(perp_intent, market, is_taker=False))

        if spot_ok and perp_ok:
            return PairResult(outcome=PairOutcome.BOTH_FILLED, trades=trades)

        if not spot_ok and not perp_ok:
            return PairResult(outcome=PairOutcome.BOTH_FAILED, trades=trades)

        # 单腿: 模拟 unwind (反向 taker market 平掉已成交腿)
        leg_filled = trades[0]
        unwind = self._build_unwind_trade(leg_filled, market)
        if spot_ok:
            return PairResult(
                outcome=PairOutcome.SPOT_ONLY_UNWOUND,
                trades=trades,
                unwind_trade=unwind,
            )
        return PairResult(
            outcome=PairOutcome.PERP_ONLY_UNWOUND,
            trades=trades,
            unwind_trade=unwind,
        )

    # ------------------------------------------------------------------
    # DRY_RUN / LIVE 模式 (Phase E.2+ 实现, 此处骨架)
    # ------------------------------------------------------------------

    async def _execute_dry_run(
        self,
        spot_intent: OrderIntent,
        perp_intent: OrderIntent,
        market: MarketState,
    ) -> PairResult:
        if self.broker is None:
            raise RuntimeError("DRY_RUN mode requires broker_adapter")
        await self.broker.place_pair_dry(spot_intent, perp_intent)
        trades = [
            self._mock_fill(spot_intent, market, is_taker=False),
            self._mock_fill(perp_intent, market, is_taker=False),
        ]
        return PairResult(outcome=PairOutcome.BOTH_FILLED, trades=trades)

    async def _execute_live(
        self,
        spot_intent: OrderIntent,
        perp_intent: OrderIntent,
        market: MarketState,
    ) -> PairResult:
        if self.broker is None:
            raise RuntimeError("LIVE mode requires broker_adapter")

        spot_task = asyncio.create_task(self.broker.place_order(spot_intent))
        perp_task = asyncio.create_task(self.broker.place_order(perp_intent))

        try:
            done, pending = await asyncio.wait(
                [spot_task, perp_task],
                timeout=self.unwind_timeout,
                return_when=asyncio.ALL_COMPLETED,
            )
        except Exception as e:
            logger.exception("dgr_btc_atomic_live_dispatch_error", error=str(e))
            return PairResult(outcome=PairOutcome.BOTH_FAILED)

        spot_trade: Optional[Trade] = None
        perp_trade: Optional[Trade] = None
        spot_exc: Optional[BaseException] = None
        perp_exc: Optional[BaseException] = None
        try:
            if spot_task.done():
                spot_exc = spot_task.exception()
                if spot_exc is None:
                    spot_trade = spot_task.result()
        except Exception:
            pass
        try:
            if perp_task.done():
                perp_exc = perp_task.exception()
                if perp_exc is None:
                    perp_trade = perp_task.result()
        except Exception:
            pass

        # Phase G.3: 详细 log 每条 leg 的结果 (区分 fill / reject / exception)
        if spot_exc is not None:
            logger.warning(
                "dgr_btc_atomic_spot_leg_failed",
                exc_type=type(spot_exc).__name__,
                exc_msg=str(spot_exc)[:200],
                intent_side=spot_intent.side.value,
                intent_price=str(spot_intent.price),
                intent_qty=str(spot_intent.quantity),
            )
        if perp_exc is not None:
            logger.warning(
                "dgr_btc_atomic_perp_leg_failed",
                exc_type=type(perp_exc).__name__,
                exc_msg=str(perp_exc)[:200],
                intent_side=perp_intent.side.value,
                intent_price=str(perp_intent.price),
                intent_qty=str(perp_intent.quantity),
            )

        if spot_trade is not None and perp_trade is not None:
            return PairResult(
                outcome=PairOutcome.BOTH_FILLED,
                trades=[spot_trade, perp_trade],
            )

        # 单腿: cancel pending + unwind 已成交腿
        for task in pending:
            task.cancel()
        if spot_trade is None and perp_trade is None:
            return PairResult(outcome=PairOutcome.BOTH_FAILED)

        filled = spot_trade if spot_trade is not None else perp_trade
        assert filled is not None
        unwind = await self._unwind_via_broker(filled, market)
        return PairResult(
            outcome=(
                PairOutcome.SPOT_ONLY_UNWOUND
                if spot_trade is not None
                else PairOutcome.PERP_ONLY_UNWOUND
            ),
            trades=[filled],
            unwind_trade=unwind,
        )

    # ------------------------------------------------------------------
    # 辅助 — mock fill 与 unwind 构造
    # ------------------------------------------------------------------

    def _mock_fill(
        self,
        intent: OrderIntent,
        market: MarketState,
        is_taker: bool = False,
    ) -> Trade:
        notional = intent.price * intent.quantity
        if intent.market == MarketType.SPOT:
            fee = notional * self.spot_fee
        else:
            fee = notional * (self.perp_taker_fee if is_taker else self.perp_maker_fee)
        return Trade(
            trade_id=f"dgr_{_uuid.uuid4().hex[:12]}",
            order_id=f"paper_{_uuid.uuid4().hex[:8]}",
            symbol="BTC/USDT" if intent.market == MarketType.SPOT else "BTC/USDT:USDT",
            market=intent.market,
            side=intent.side,
            price=intent.price,
            quantity=intent.quantity,
            fee=fee,
            is_maker=not is_taker,
            timestamp=market.timestamp,
            grid_level=intent.grid_level,
        )

    def _build_unwind_trade(self, leg: Trade, market: MarketState) -> Trade:
        opposite_side = Side.SELL if leg.side == Side.BUY else Side.BUY
        notional = leg.price * leg.quantity
        if leg.market == MarketType.SPOT:
            fee = notional * self.spot_fee
        else:
            fee = notional * self.perp_taker_fee
        return Trade(
            trade_id=f"unwind_{_uuid.uuid4().hex[:12]}",
            order_id=f"unwind_{_uuid.uuid4().hex[:8]}",
            symbol=leg.symbol,
            market=leg.market,
            side=opposite_side,
            price=leg.price,
            quantity=leg.quantity,
            fee=fee,
            is_maker=False,
            timestamp=market.timestamp,
            grid_level=leg.grid_level,
        )

    async def _unwind_via_broker(self, leg: Trade, market: MarketState) -> Optional[Trade]:
        """C8 修复 (LIVE 审计): unwind 失败时 trigger_kill 自动止损 + Telegram alert,
        不再静默 return None 让上层误判'已 unwound'.
        """
        if self.broker is None:
            return None
        try:
            opposite = Side.SELL if leg.side == Side.BUY else Side.BUY
            unwind_trade = await self.broker.place_market_unwind(
                market_type=leg.market,
                side=opposite,
                quantity=leg.quantity,
            )
            if unwind_trade is None:
                raise RuntimeError("place_market_unwind returned None")
            return unwind_trade
        except Exception as e:
            logger.exception(
                "dgr_btc_unwind_broker_failed_TRIGGER_KILL",
                error=str(e)[:200],
                leg_market=leg.market.value, leg_side=leg.side.value,
                leg_qty=str(leg.quantity), leg_price=str(leg.price),
            )
            # 自动 trigger_kill 阻止后续派单堆积单腿
            safety = getattr(self.broker, "safety", None)
            if safety is not None:
                try:
                    safety.trigger_kill(f"unwind_failed:{leg.market.value}:{leg.quantity}")
                except Exception:
                    logger.exception("dgr_btc_unwind_kill_trigger_failed")
            # 不返回伪 Trade — 上层必须感知 unwind 失败
            return None

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "n_pairs_total": self.n_pairs_total,
            "n_both_filled": self.n_both_filled,
            "n_single_leg_unwound": self.n_single_leg_unwound,
            "n_pretrade_rejected": self.n_pretrade_rejected,
            "mode": self.mode.value,
        }


def pair_intents(intents: list[OrderIntent]) -> list[tuple[OrderIntent, OrderIntent]]:
    """把 strategy.on_tick 输出的 flat intents 配对成 (spot, perp) 对.

    dgr_btc 的 _gen_sell_pair / _gen_buy_pair 总是同时返回 spot + perp 同 grid_level,
    所以按 grid_level 分组 + 每组按 market 拆 (spot, perp).
    """
    groups: dict[Decimal, dict[MarketType, OrderIntent]] = {}
    for it in intents:
        gl = it.grid_level
        groups.setdefault(gl, {})[it.market] = it
    pairs: list[tuple[OrderIntent, OrderIntent]] = []
    for gl, by_market in groups.items():
        spot = by_market.get(MarketType.SPOT)
        perp = by_market.get(MarketType.PERP)
        if spot is not None and perp is not None:
            pairs.append((spot, perp))
        else:
            logger.warning(
                "dgr_btc_atomic_orphan_intent",
                grid_level=str(gl),
                has_spot=spot is not None,
                has_perp=perp is not None,
            )
    return pairs
