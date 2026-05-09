"""SpotPerpRunner — 后台扫描循环,按间隔调 SpotPerpBasisScanner。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.strategies.spot_perp_basis.scanner import (
    SpotPerpBasisScanner,
    SpotPerpOpportunity,
)

logger = get_logger(__name__)


class SpotPerpRunner:
    """spot-perp 基差扫描循环,机会列表保存在内存供 API 暴露。"""

    def __init__(
        self,
        scanner: SpotPerpBasisScanner,
        scan_interval_seconds: float = 60.0,
    ) -> None:
        self._scanner = scanner
        self._scan_interval = scan_interval_seconds
        self._latest_opportunities: list[SpotPerpOpportunity] = []
        self._last_scan_at: datetime | None = None
        self._stop_event = asyncio.Event()
        self._running = False

    @property
    def latest_opportunities(self) -> list[SpotPerpOpportunity]:
        return self._latest_opportunities

    @property
    def last_scan_at(self) -> datetime | None:
        return self._last_scan_at

    @property
    def is_running(self) -> bool:
        return self._running

    async def run_forever(self) -> None:
        from time import perf_counter  # noqa: PLC0415
        from app.core.metrics import get_metrics  # noqa: PLC0415
        self._running = True
        logger.info("spot_perp_runner_started", interval=self._scan_interval)
        try:
            while not self._stop_event.is_set():
                try:
                    scan_start = perf_counter()
                    opps = await self._scanner.scan_once()
                    get_metrics().record_scan(
                        "spot_perp", (perf_counter() - scan_start) * 1000,
                    )
                    self._latest_opportunities = opps
                    self._last_scan_at = datetime.now(timezone.utc)
                    if opps:
                        logger.info(
                            "spot_perp_scan_complete",
                            count=len(opps),
                            top_symbol=opps[0].symbol,
                            top_basis_pct=str(opps[0].basis_pct),
                        )
                except Exception:
                    logger.exception("spot_perp_scan_failed")

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._scan_interval
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False
            logger.info("spot_perp_runner_stopped")

    def stop(self) -> None:
        self._stop_event.set()
