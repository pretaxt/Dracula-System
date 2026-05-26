"""
dgr_btc/maker_reprice.py
========================
MakerReprice — LIMIT_MAKER reject 后的重挂逻辑 (Phase E.2).

Codex 讨论结论 (用户 sign-off):
  - 默认只挂 LIMIT_MAKER (post_only)
  - reject → 外侧 1 tick 重挂
  - 最多 3 次, 失败 → 弃单 (不 fallback taker)
  - 下次 grid 穿越自然再触发, 网格策略错过一次没事

Taker fallback 留给 risk-engine 触发的 EMERGENCY reduce-only 路径 (Phase E.2b 未在本文件).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

import structlog

from app.strategies.dgr_btc.strategy_core import OrderIntent
from app.strategies.dgr_btc.types import MarketType, Side, Trade

logger = structlog.get_logger(__name__)


class RepriceOutcome(str, Enum):
    FILLED_FIRST_TRY = "FILLED_FIRST_TRY"
    FILLED_AFTER_REPRICE = "FILLED_AFTER_REPRICE"
    GIVE_UP_AFTER_MAX_ATTEMPTS = "GIVE_UP_AFTER_MAX_ATTEMPTS"
    BROKER_EXCEPTION = "BROKER_EXCEPTION"


@dataclass
class RepriceResult:
    outcome: RepriceOutcome
    trade: Optional[Trade] = None
    attempts: int = 0
    final_price: Optional[Decimal] = None
    reject_history: list[str] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.reject_history is None:
            self.reject_history = []


class MakerReprice:
    """LIMIT_MAKER 单的下单 + reject 重挂 wrapper."""

    SPOT_TICK = Decimal("0.01")
    PERP_TICK = Decimal("0.1")

    def __init__(
        self,
        broker_adapter: Any,
        max_attempts: int = 3,
        reprice_delay_ms: int = 300,
        tick_offset: int = 1,
    ) -> None:
        self.broker = broker_adapter
        self.max_attempts = max_attempts
        self.reprice_delay_ms = reprice_delay_ms
        self.tick_offset = tick_offset
        self.n_calls = 0
        self.n_filled_first = 0
        self.n_filled_after_reprice = 0
        self.n_give_up = 0
        self.n_broker_exception = 0

    async def place(
        self,
        intent: OrderIntent,
    ) -> RepriceResult:
        """下单 + reject 时重挂. broker 必须支持 place_limit_maker → 返回 trade or raise RejectError."""
        self.n_calls += 1
        attempts = 0
        current_price = intent.price
        reject_history: list[str] = []

        while attempts < self.max_attempts:
            attempts += 1
            try:
                trade = await self._try_place(intent, current_price)
            except RejectError as e:
                reject_history.append(f"attempt {attempts}: {e.reason}")
                logger.info(
                    "dgr_btc_maker_reject",
                    attempt=attempts,
                    max=self.max_attempts,
                    reason=e.reason,
                    price=str(current_price),
                    market=intent.market.value,
                )
                if attempts >= self.max_attempts:
                    break
                current_price = self._adjust_price(intent, current_price)
                await asyncio.sleep(self.reprice_delay_ms / 1000)
                continue
            except Exception as e:
                self.n_broker_exception += 1
                logger.exception(
                    "dgr_btc_maker_broker_exception",
                    error=str(e)[:200],
                    attempt=attempts,
                )
                return RepriceResult(
                    outcome=RepriceOutcome.BROKER_EXCEPTION,
                    attempts=attempts,
                    final_price=current_price,
                    reject_history=reject_history,
                )

            # 成交
            if attempts == 1:
                self.n_filled_first += 1
                outcome = RepriceOutcome.FILLED_FIRST_TRY
            else:
                self.n_filled_after_reprice += 1
                outcome = RepriceOutcome.FILLED_AFTER_REPRICE
            return RepriceResult(
                outcome=outcome,
                trade=trade,
                attempts=attempts,
                final_price=current_price,
                reject_history=reject_history,
            )

        # 用尽 attempts 仍 reject
        self.n_give_up += 1
        logger.warning(
            "dgr_btc_maker_give_up",
            attempts=attempts,
            reject_history=reject_history[-3:],
            market=intent.market.value,
            grid_level=str(intent.grid_level),
        )
        return RepriceResult(
            outcome=RepriceOutcome.GIVE_UP_AFTER_MAX_ATTEMPTS,
            attempts=attempts,
            final_price=current_price,
            reject_history=reject_history,
        )

    async def _try_place(self, intent: OrderIntent, price: Decimal) -> Trade:
        """单次下单尝试, broker.place_limit_maker 必须 raise RejectError 或返回 Trade."""
        return await self.broker.place_limit_maker(
            market=intent.market,
            side=intent.side,
            price=price,
            quantity=intent.quantity,
        )

    def _adjust_price(self, intent: OrderIntent, current: Decimal) -> Decimal:
        """LIMIT_MAKER reject = 价格穿越; 外侧 1 tick 重挂. SELL 上推, BUY 下推."""
        tick = self.SPOT_TICK if intent.market == MarketType.SPOT else self.PERP_TICK
        offset = tick * Decimal(self.tick_offset)
        if intent.side == Side.SELL:
            return current + offset
        return current - offset

    def stats(self) -> dict:
        return {
            "n_calls": self.n_calls,
            "n_filled_first": self.n_filled_first,
            "n_filled_after_reprice": self.n_filled_after_reprice,
            "n_give_up": self.n_give_up,
            "n_broker_exception": self.n_broker_exception,
            "max_attempts": self.max_attempts,
        }


class RejectError(Exception):
    """LIMIT_MAKER 被 broker reject (post-only 穿越). broker_adapter 应该 raise 此异常."""

    def __init__(self, reason: str, original_error: Optional[Exception] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.original_error = original_error
