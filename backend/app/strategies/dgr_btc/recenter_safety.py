"""
dgr_btc/recenter_safety.py
==========================
RecenterCancelManager — Recenter 时撤单失败处理 (Phase E.4).

问题:
  Recenter 触发 → 撤旧 grid 挂单 → rebuild_around(new_center) → 下新单.
  若撤单失败 (broker reject / 网络中断), 旧单仍在盘口 + 新单也下了
  → 同一价位双层挂单 → 仓位暴露 2x → 严重风险.

解决:
  1. 撤单失败 → 指数 backoff 重试 (max 3 次)
  2. 仍失败 → 不允许 rebuild_around, halt strategy + emergency Telegram
  3. 人工介入清理旧单后, 调 PATCH /strategies/dgr-btc/resume 解除 halt

PAPER 模式: 跳过 (paper 没真实挂单), only_live=True
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class CancelOutcome(str, Enum):
    ALL_CANCELED = "ALL_CANCELED"
    PARTIAL_CANCELED_HALT = "PARTIAL_CANCELED_HALT"
    NONE_CANCELED_HALT = "NONE_CANCELED_HALT"
    BROKER_TIMEOUT_HALT = "BROKER_TIMEOUT_HALT"
    SKIPPED_PAPER_MODE = "SKIPPED_PAPER_MODE"


@dataclass
class CancelReport:
    timestamp: datetime
    outcome: CancelOutcome
    n_orders_total: int
    n_canceled: int
    n_failed: int
    failed_order_ids: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    notes: str = ""


class RecenterCancelManager:
    def __init__(
        self,
        broker_adapter: Any | None = None,
        max_retries: int = 3,
        retry_delay_ms: int = 500,
        cancel_timeout_sec: float = 10.0,
        live_mode: bool = False,
        halt_callback: Any | None = None,
    ) -> None:
        self.broker = broker_adapter
        self.max_retries = max_retries
        self.retry_delay_ms = retry_delay_ms
        self.cancel_timeout = cancel_timeout_sec
        self.live_mode = live_mode
        self.halt_callback = halt_callback
        self.halted = False
        self.halt_reason: Optional[str] = None
        self.n_recenter_attempts = 0
        self.n_successful_cancels = 0
        self.n_halt_events = 0

    async def cancel_open_orders_for_recenter(self) -> CancelReport:
        """撤掉所有 dgr_btc grid 的 open orders. 失败 → halt strategy."""
        import time as _t
        t0 = _t.monotonic()
        self.n_recenter_attempts += 1

        # PAPER 模式: 跳过
        if not self.live_mode or self.broker is None:
            return CancelReport(
                timestamp=datetime.now(timezone.utc),
                outcome=CancelOutcome.SKIPPED_PAPER_MODE,
                n_orders_total=0,
                n_canceled=0,
                n_failed=0,
                elapsed_ms=int((_t.monotonic() - t0) * 1000),
                notes="paper mode no real orders to cancel",
            )

        # 1. 拉所有 open orders
        try:
            open_orders = await asyncio.wait_for(
                self._fetch_open_orders(),
                timeout=self.cancel_timeout,
            )
        except asyncio.TimeoutError:
            await self._halt("fetch_open_orders_timeout")
            return CancelReport(
                timestamp=datetime.now(timezone.utc),
                outcome=CancelOutcome.BROKER_TIMEOUT_HALT,
                n_orders_total=0,
                n_canceled=0,
                n_failed=0,
                elapsed_ms=int((_t.monotonic() - t0) * 1000),
                notes="broker fetch_open_orders timed out",
            )
        except Exception as e:
            logger.exception("dgr_btc_recenter_fetch_failed", error=str(e)[:200])
            await self._halt(f"fetch_failed: {str(e)[:100]}")
            return CancelReport(
                timestamp=datetime.now(timezone.utc),
                outcome=CancelOutcome.BROKER_TIMEOUT_HALT,
                n_orders_total=0,
                n_canceled=0,
                n_failed=0,
                elapsed_ms=int((_t.monotonic() - t0) * 1000),
                notes=f"fetch exception: {str(e)[:100]}",
            )

        if not open_orders:
            return CancelReport(
                timestamp=datetime.now(timezone.utc),
                outcome=CancelOutcome.ALL_CANCELED,
                n_orders_total=0,
                n_canceled=0,
                n_failed=0,
                elapsed_ms=int((_t.monotonic() - t0) * 1000),
                notes="no open orders",
            )

        # 2. 逐个撤单 + 重试
        n_total = len(open_orders)
        canceled: list[str] = []
        failed: list[str] = []
        for order in open_orders:
            order_id = order.get("id") if isinstance(order, dict) else str(order)
            ok = await self._cancel_with_retry(order_id)
            if ok:
                canceled.append(order_id)
            else:
                failed.append(order_id)

        self.n_successful_cancels += len(canceled)
        elapsed_ms = int((_t.monotonic() - t0) * 1000)

        if not failed:
            return CancelReport(
                timestamp=datetime.now(timezone.utc),
                outcome=CancelOutcome.ALL_CANCELED,
                n_orders_total=n_total,
                n_canceled=len(canceled),
                n_failed=0,
                elapsed_ms=elapsed_ms,
            )

        # 部分或全部失败 → halt
        outcome = (
            CancelOutcome.NONE_CANCELED_HALT
            if not canceled
            else CancelOutcome.PARTIAL_CANCELED_HALT
        )
        reason = f"recenter cancel: {len(failed)}/{n_total} failed; failed_ids={failed[:5]}"
        await self._halt(reason)
        return CancelReport(
            timestamp=datetime.now(timezone.utc),
            outcome=outcome,
            n_orders_total=n_total,
            n_canceled=len(canceled),
            n_failed=len(failed),
            failed_order_ids=failed,
            elapsed_ms=elapsed_ms,
            notes=reason,
        )

    async def _cancel_with_retry(self, order_id: str) -> bool:
        """单笔撤单 + max_retries 次重试. 返回 True 表示最终成功."""
        for attempt in range(1, self.max_retries + 1):
            try:
                await asyncio.wait_for(
                    self.broker.cancel_order(order_id),
                    timeout=self.cancel_timeout,
                )
                return True
            except Exception as e:
                logger.warning(
                    "dgr_btc_cancel_attempt_failed",
                    order_id=order_id,
                    attempt=attempt,
                    max=self.max_retries,
                    error=str(e)[:120],
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_delay_ms / 1000)
        return False

    async def _fetch_open_orders(self) -> list:
        if self.broker is None:
            return []
        return await self.broker.fetch_open_orders()

    async def _halt(self, reason: str) -> None:
        """触发 strategy halt + Telegram emergency."""
        if self.halted:
            return
        self.halted = True
        self.halt_reason = reason
        self.n_halt_events += 1
        logger.error(
            "dgr_btc_recenter_safety_halt",
            reason=reason,
            n_halt_events=self.n_halt_events,
        )
        if self.halt_callback is not None:
            try:
                await self.halt_callback(reason)
            except Exception as e:
                logger.exception("dgr_btc_halt_callback_failed", error=str(e)[:120])

        try:
            from app.notifications.telegram import notify_risk_violation
            notify_risk_violation(
                rule="dgr_btc_recenter_halt",
                message=(
                    f"🚨 HALT: Recenter 撤单失败, 策略已停手避免双层挂单暴露 2x:\n"
                    f"原因: {reason}\n"
                    f"需要人工: 1) 登录交易所确认/撤掉残留 grid 单 "
                    f"2) PATCH /strategies/dgr-btc/resume 解除 halt"
                ),
            )
        except Exception as e:
            logger.warning("dgr_btc_halt_alert_failed", error=str(e)[:120])

    def resume(self) -> None:
        """人工介入清理后调用. 解除 halt, 允许下次 recenter 重试."""
        if not self.halted:
            return
        prev_reason = self.halt_reason
        self.halted = False
        self.halt_reason = None
        logger.info("dgr_btc_recenter_safety_resumed", prev_reason=prev_reason)

    def is_halted(self) -> bool:
        return self.halted

    def stats(self) -> dict:
        return {
            "n_recenter_attempts": self.n_recenter_attempts,
            "n_successful_cancels": self.n_successful_cancels,
            "n_halt_events": self.n_halt_events,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "max_retries": self.max_retries,
        }
