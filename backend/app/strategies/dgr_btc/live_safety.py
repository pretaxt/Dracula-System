"""
dgr_btc/live_safety.py
======================
LiveSafetyGuard — Phase F.2 LIVE 模式 hard guards.

防御层 (按触发顺序):
  1. kill_switch: 文件存在 → 全停 (运维紧急关停)
  2. max_order_usd: 单笔上限 (防错单)
  3. max_daily_notional_usd: 日累计上限
  4. max_daily_order_count: 日笔数上限 (防 API 频率失控)
  5. max_price_deviation_pct: 价格偏离 ticker 上限 (防错价)
  6. audit_log: 每笔成交写 jsonl (溯源)

设计:
  - guard 失败立即 raise / 返回 (False, reason), 不静默
  - daily counter 跨 UTC 0:00 自动 reset
  - audit jsonl 文件 append-only, 不删
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, date
from decimal import Decimal
from pathlib import Path
from typing import Optional

import structlog

from app.strategies.dgr_btc.types import MarketType, Side

logger = structlog.get_logger(__name__)


@dataclass
class LiveSafetyConfig:
    enabled: bool = True
    max_order_usd: Decimal = Decimal("100")
    max_daily_notional_usd: Decimal = Decimal("2000")
    max_daily_order_count: int = 200
    max_price_deviation_pct: Decimal = Decimal("0.005")  # 0.5% from mark
    # CRITICAL #3 fix: PM cross margin BUY 自动借 USDT 时的累计上限
    # papi_get_balance().userAssets[USDT].crossMarginBorrowed + new_notional <= cap
    max_open_borrow_usd: Decimal = Decimal("5000")
    kill_switch_path: str = "/app/state/dgr_btc_KILL"
    audit_log_path: str = "/app/state/dgr_btc_audit.jsonl"


class LiveSafetyGuard:
    def __init__(self, cfg: Optional[LiveSafetyConfig] = None) -> None:
        self.cfg = cfg or LiveSafetyConfig()
        self._daily_notional: Decimal = Decimal("0")
        self._daily_count: int = 0
        # C3 修复 (LIVE 审计): pending reservation 防并发突破 daily cap
        # check_pre_order 预扣 → record_filled commit / rollback_reservation 释放
        self._pending_notional: Decimal = Decimal("0")
        self._pending_count: int = 0
        self._counter_date: date = date.today()
        # asyncio.Lock 保护 counter 整体一致性
        import asyncio as _aio
        self._lock = _aio.Lock()
        self.n_check = 0
        self.n_block_killswitch = 0
        self.n_block_max_order = 0
        self.n_block_daily_notional = 0
        self.n_block_daily_count = 0
        self.n_block_price_dev = 0
        # CRITICAL #3: 借贷余额 cache, 由 paper_trading._tick 周期刷新 (30s)
        self.n_block_borrow_cap = 0
        self._cached_borrowed_usdt: Decimal = Decimal("0")
        self._borrowed_cache_ts: Optional[datetime] = None

    # ------------------------------------------------------------------
    # 检查接口
    # ------------------------------------------------------------------

    def check_pre_order(
        self,
        market_type: MarketType,
        side: Side,
        price: Decimal,
        quantity: Decimal,
        mark_price: Optional[Decimal] = None,
    ) -> tuple[bool, Optional[str]]:
        """下单前检查. 返回 (ok, reason). ok=False 则不应下单.

        C3 修复: 同步原子检查 (无 await), 计入 pending reservation;
        外部并发调用按 GIL 串行化, daily cap 不会被穿越.
        调用方:
          - 派单成功 → record_filled(notional) commit reservation 到 daily
          - 派单失败/reject → rollback_reservation(notional) 释放 pending
        """
        self.n_check += 1
        if not self.cfg.enabled:
            return True, None

        self._maybe_reset_daily()

        # 1. kill_switch
        if self._is_killed():
            self.n_block_killswitch += 1
            return False, "kill_switch_active"

        notional = price * quantity

        # 2. max_order_usd
        if notional > self.cfg.max_order_usd:
            self.n_block_max_order += 1
            return False, f"max_order_usd ${notional} > ${self.cfg.max_order_usd}"

        # 3. max_daily_notional_usd (含 pending reservation)
        total_notional = self._daily_notional + self._pending_notional + notional
        if total_notional > self.cfg.max_daily_notional_usd:
            self.n_block_daily_notional += 1
            return False, (
                f"max_daily_notional commit ${self._daily_notional}+pending ${self._pending_notional}"
                f"+new ${notional} = ${total_notional} > ${self.cfg.max_daily_notional_usd}"
            )

        # 4. max_daily_order_count (含 pending)
        total_count = self._daily_count + self._pending_count + 1
        if total_count > self.cfg.max_daily_order_count:
            self.n_block_daily_count += 1
            return False, (
                f"max_daily_order_count commit {self._daily_count}+pending {self._pending_count}"
                f"+1 = {total_count} > {self.cfg.max_daily_order_count}"
            )

        # 5. max_price_deviation_pct (vs mark)
        if mark_price is not None and mark_price > 0:
            dev = abs(price - mark_price) / mark_price
            if dev > self.cfg.max_price_deviation_pct:
                self.n_block_price_dev += 1
                return False, f"price_deviation {dev:.4f} > {self.cfg.max_price_deviation_pct}"

        # CRITICAL #3: PM cross margin BUY 自动借 USDT 累计上限
        # 仅对 spot BUY 检查 (perp 不涉及现货借贷; spot SELL 走 AUTO_REPAY 是还款不是借)
        if market_type == MarketType.SPOT and side == Side.BUY:
            projected_borrow = self._cached_borrowed_usdt + notional
            if projected_borrow > self.cfg.max_open_borrow_usd:
                self.n_block_borrow_cap += 1
                return False, (
                    f"max_open_borrow_usd cached_borrowed=${self._cached_borrowed_usdt} "
                    f"+ new=${notional} = ${projected_borrow} > ${self.cfg.max_open_borrow_usd}"
                )

        # C3: 预扣 reservation (派单 in-flight, 即使未 fill 也占 cap)
        self._pending_notional += notional
        self._pending_count += 1

        return True, None

    def update_borrowed_usdt(self, borrowed: Decimal) -> None:
        """CRITICAL #3: paper_trading._tick 周期调用 (30s), 刷新 USDT 借款 cache.

        来源: spot_client.papi_get_balance() 找 USDT.crossMarginBorrowed.
        """
        self._cached_borrowed_usdt = borrowed
        self._borrowed_cache_ts = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        """CRITICAL #2: persist daily counters across restart (within same UTC day).

        pending_* 故意不持久化: 进程死时 in-flight reservation 对应的派单 future 已丢,
        无法 commit/rollback. 重启时 pending=0 是正确的(只有未来新 reservation 才计)。
        """
        return {
            "daily_notional": str(self._daily_notional),
            "daily_count": self._daily_count,
            "counter_date": str(self._counter_date),
        }

    def restore_from_dict(self, d: dict) -> None:
        """CRITICAL #2: 启动时恢复 daily counters, 仅在 counter_date == today (UTC) 时."""
        try:
            saved_date_str = str(d.get("counter_date", "1970-01-01"))
            saved_date = date.fromisoformat(saved_date_str[:10])
        except Exception:
            logger.warning("dgr_btc_safety_restore_parse_failed", raw=str(d)[:100])
            return
        today = datetime.now(timezone.utc).date()
        if saved_date != today:
            logger.info(
                "dgr_btc_safety_restore_skipped_stale",
                saved_date=str(saved_date), today=str(today),
            )
            return
        try:
            self._daily_notional = Decimal(str(d.get("daily_notional", "0")))
            self._daily_count = int(d.get("daily_count", 0))
            self._counter_date = saved_date
            logger.info(
                "dgr_btc_safety_counters_restored",
                daily_notional=str(self._daily_notional),
                daily_count=self._daily_count,
                date=str(self._counter_date),
            )
        except Exception as e:
            logger.warning("dgr_btc_safety_restore_failed", err=str(e)[:120])

    def record_filled(self, notional: Decimal) -> None:
        """成交后调用, commit pending reservation 到 daily 计数器."""
        self._maybe_reset_daily()
        # C3: commit — 从 pending 移到 daily
        if self._pending_notional >= notional:
            self._pending_notional -= notional
        else:
            self._pending_notional = Decimal("0")
        if self._pending_count > 0:
            self._pending_count -= 1
        self._daily_notional += notional
        self._daily_count += 1

    def rollback_reservation(self, notional: Decimal) -> None:
        """C3: 派单失败/reject 时调用, 释放 pending reservation."""
        if self._pending_notional >= notional:
            self._pending_notional -= notional
        else:
            self._pending_notional = Decimal("0")
        if self._pending_count > 0:
            self._pending_count -= 1

    def audit(self, event: dict) -> None:
        """追加一行到 audit jsonl. event 应含至少 {ts, action, ...}."""
        if not self.cfg.audit_log_path:
            return
        try:
            os.makedirs(os.path.dirname(self.cfg.audit_log_path), exist_ok=True)
            event_full = {
                "ts": datetime.now(timezone.utc).isoformat(),
                **event,
            }
            with open(self.cfg.audit_log_path, "a") as f:
                f.write(json.dumps(event_full, default=str) + "\n")
        except Exception as e:
            logger.warning("dgr_btc_audit_write_failed", error=str(e)[:120])

    # ------------------------------------------------------------------
    # kill switch (运维紧急关停)
    # ------------------------------------------------------------------

    def _is_killed(self) -> bool:
        return Path(self.cfg.kill_switch_path).exists()

    def is_killed(self) -> bool:
        return self._is_killed()

    def trigger_kill(self, reason: str = "manual") -> None:
        """运维 / 风控 触发 kill switch (写 magic file)."""
        try:
            os.makedirs(os.path.dirname(self.cfg.kill_switch_path), exist_ok=True)
            Path(self.cfg.kill_switch_path).write_text(
                f"killed at {datetime.now(timezone.utc).isoformat()}\nreason: {reason}\n"
            )
            logger.error("dgr_btc_safety_kill_triggered", reason=reason)
        except Exception as e:
            logger.exception("dgr_btc_safety_kill_write_failed", error=str(e)[:120])

    def clear_kill(self) -> None:
        try:
            Path(self.cfg.kill_switch_path).unlink(missing_ok=True)
            logger.info("dgr_btc_safety_kill_cleared")
        except Exception as e:
            logger.warning("dgr_btc_safety_kill_clear_failed", error=str(e)[:120])

    # ------------------------------------------------------------------
    # daily reset
    # ------------------------------------------------------------------

    def _maybe_reset_daily(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._counter_date:
            self._daily_notional = Decimal("0")
            self._daily_count = 0
            self._counter_date = today
            logger.info("dgr_btc_safety_daily_reset", new_date=str(today))

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        return {
            "enabled": self.cfg.enabled,
            "n_check": self.n_check,
            "n_block_killswitch": self.n_block_killswitch,
            "n_block_max_order": self.n_block_max_order,
            "n_block_daily_notional": self.n_block_daily_notional,
            "n_block_daily_count": self.n_block_daily_count,
            "n_block_price_dev": self.n_block_price_dev,
            "n_block_borrow_cap": self.n_block_borrow_cap,
            "daily_notional": str(self._daily_notional),
            "daily_count": self._daily_count,
            "pending_notional": str(self._pending_notional),
            "pending_count": self._pending_count,
            "counter_date": str(self._counter_date),
            "cached_borrowed_usdt": str(self._cached_borrowed_usdt),
            "borrowed_cache_ts": (
                self._borrowed_cache_ts.isoformat() if self._borrowed_cache_ts else None
            ),
            "is_killed": self._is_killed(),
            "limits": {
                "max_order_usd": str(self.cfg.max_order_usd),
                "max_daily_notional_usd": str(self.cfg.max_daily_notional_usd),
                "max_daily_order_count": self.cfg.max_daily_order_count,
                "max_price_deviation_pct": str(self.cfg.max_price_deviation_pct),
                "max_open_borrow_usd": str(self.cfg.max_open_borrow_usd),
            },
        }
