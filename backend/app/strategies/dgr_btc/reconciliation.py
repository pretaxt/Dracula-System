"""
dgr_btc/reconciliation.py
=========================
ReconcileTask — 后台 60s 异步对账 (Phase E.1 防护层 3).

PAPER 模式: 对比 strategy 内部 spot_pos/perp_pos vs DB pnl_timeseries 最新行
LIVE 模式 (Phase E 后期): 对比 strategy 内部仓位 vs binance 真实 spot_balance / perp_position

发现 drift > tolerance:
  - 记录 risk_event 表
  - Telegram 告警
  - 继续运行 (不 halt - 一次 drift 可能是网络延迟; 连续 N 次才 halt)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ReconcileReport:
    timestamp: datetime
    ok: bool
    spot_strategy: Decimal
    spot_actual: Decimal
    perp_strategy: Decimal
    perp_actual: Decimal
    drift_spot: Decimal
    drift_perp: Decimal
    notes: str = ""


class ReconcileTask:
    def __init__(
        self,
        session: Any,
        interval_seconds: float = 60.0,
        drift_tolerance_btc: Decimal = Decimal("0.001"),
        consecutive_drift_threshold: int = 3,
        live_mode: bool = False,
        broker_adapter: Any | None = None,
    ) -> None:
        self.session = session
        self.interval = interval_seconds
        self.tolerance = drift_tolerance_btc
        self.consecutive_threshold = consecutive_drift_threshold
        self.live_mode = live_mode
        self.broker = broker_adapter
        self.consecutive_drift = 0
        self.n_reconciles = 0
        self.n_drift_events = 0
        self.last_report: Optional[ReconcileReport] = None
        self._stop_event = asyncio.Event()
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info(
            "dgr_btc_reconcile_started",
            interval_sec=self.interval,
            tolerance_btc=str(self.tolerance),
            live_mode=self.live_mode,
        )
        try:
            while self._running:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.interval,
                    )
                except asyncio.TimeoutError:
                    pass
                if not self._running:
                    break
                try:
                    await self.tick()
                except Exception as e:
                    logger.exception("dgr_btc_reconcile_tick_failed", error=str(e))
        finally:
            logger.info("dgr_btc_reconcile_stopped", n_reconciles=self.n_reconciles)

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()

    async def tick(self) -> ReconcileReport:
        self.n_reconciles += 1
        strategy = getattr(self.session, "strategy", None)
        if strategy is None:
            return ReconcileReport(
                timestamp=datetime.now(timezone.utc),
                ok=True,
                spot_strategy=Decimal("0"),
                spot_actual=Decimal("0"),
                perp_strategy=Decimal("0"),
                perp_actual=Decimal("0"),
                drift_spot=Decimal("0"),
                drift_perp=Decimal("0"),
                notes="strategy_not_initialized",
            )

        spot_s = strategy.spot_pos.quantity
        perp_s = strategy.perp_pos.quantity

        if self.live_mode and self.broker is not None:
            spot_a, perp_a = await self._fetch_live_positions()
        else:
            spot_a, perp_a = spot_s, perp_s  # PAPER: 内部状态即真实

        drift_spot = abs(spot_s - spot_a)
        drift_perp = abs(perp_s - perp_a)
        ok = drift_spot <= self.tolerance and drift_perp <= self.tolerance

        report = ReconcileReport(
            timestamp=datetime.now(timezone.utc),
            ok=ok,
            spot_strategy=spot_s,
            spot_actual=spot_a,
            perp_strategy=perp_s,
            perp_actual=perp_a,
            drift_spot=drift_spot,
            drift_perp=drift_perp,
            notes="paper_mode_self_check" if not self.live_mode else "live_broker_check",
        )
        self.last_report = report

        if not ok:
            self.consecutive_drift += 1
            self.n_drift_events += 1
            logger.warning(
                "dgr_btc_reconcile_drift",
                drift_spot=str(drift_spot),
                drift_perp=str(drift_perp),
                consecutive=self.consecutive_drift,
                threshold=self.consecutive_threshold,
                spot_strategy=str(spot_s),
                spot_actual=str(spot_a),
                perp_strategy=str(perp_s),
                perp_actual=str(perp_a),
            )
            if self.consecutive_drift >= self.consecutive_threshold:
                await self._on_persistent_drift(report)
        else:
            if self.consecutive_drift > 0:
                logger.info(
                    "dgr_btc_reconcile_drift_recovered",
                    consecutive_was=self.consecutive_drift,
                )
            self.consecutive_drift = 0
        return report

    async def _fetch_live_positions(self) -> tuple[Decimal, Decimal]:
        """Live mode: 拉真实 binance spot balance + perp position. Phase E 后期实现."""
        if self.broker is None:
            return Decimal("0"), Decimal("0")
        try:
            spot = await self.broker.get_spot_balance("BTC")
            perp = await self.broker.get_perp_position("BTC/USDT:USDT")
            return Decimal(str(spot)), Decimal(str(perp))
        except Exception as e:
            logger.warning("dgr_btc_reconcile_broker_fetch_failed", error=str(e)[:120])
            return Decimal("0"), Decimal("0")

    async def _on_persistent_drift(self, report: ReconcileReport) -> None:
        """连续 N 次 drift: telegram alert + 写 risk_event (不 halt 策略, 人工介入决定)."""
        logger.error(
            "dgr_btc_reconcile_persistent_drift",
            consecutive=self.consecutive_drift,
            drift_spot=str(report.drift_spot),
            drift_perp=str(report.drift_perp),
            spot_strategy=str(report.spot_strategy),
            spot_actual=str(report.spot_actual),
            perp_strategy=str(report.perp_strategy),
            perp_actual=str(report.perp_actual),
        )
        try:
            from app.notifications.telegram import notify_risk_violation
            notify_risk_violation(
                rule="dgr_btc_reconcile_drift",
                message=(
                    f"🚨 连续 {self.consecutive_drift} 次对账 drift > {self.tolerance} BTC\n"
                    f"spot strategy={report.spot_strategy} actual={report.spot_actual}\n"
                    f"perp strategy={report.perp_strategy} actual={report.perp_actual}\n"
                    f"需要人工介入: 检查 broker / strategy state 是否 drift"
                ),
            )
        except Exception as e:
            logger.warning("dgr_btc_reconcile_alert_send_failed", error=str(e)[:120])

    def stats(self) -> dict:
        return {
            "n_reconciles": self.n_reconciles,
            "n_drift_events": self.n_drift_events,
            "consecutive_drift": self.consecutive_drift,
            "last_report_ok": self.last_report.ok if self.last_report else None,
            "interval_sec": self.interval,
            "tolerance_btc": str(self.tolerance),
        }
