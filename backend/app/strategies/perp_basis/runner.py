"""#02 perp-basis runner — 周期性扫描 + 暴露最新机会。

scanner 同步无 IO（数据全在 hub），所以这个 runner 主要做：
  - 间隔触发 scan
  - 缓存 latest_opportunities 供 API 读
  - 记 last_scan_at + perf metric
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import perf_counter

from app.core.logging import get_logger
from app.core.metrics import get_metrics
from app.strategies.perp_basis.scanner import (
    PerpBasisOpportunity,
    PerpBasisScanner,
)

logger = get_logger(__name__)


class PerpBasisRunner:
    def __init__(
        self,
        scanner: PerpBasisScanner,
        scan_interval_seconds: float = 30.0,
    ) -> None:
        self._scanner = scanner
        self._interval = scan_interval_seconds
        self._latest_opportunities: list[PerpBasisOpportunity] = []
        self._last_scan_at: datetime | None = None
        self._stop_event = asyncio.Event()
        self._running = False

    @property
    def latest_opportunities(self) -> list[PerpBasisOpportunity]:
        return self._latest_opportunities

    @property
    def last_scan_at(self) -> datetime | None:
        return self._last_scan_at

    @property
    def is_running(self) -> bool:
        return self._running

    async def run_forever(self) -> None:
        self._running = True
        logger.info("perp_basis_runner_started", interval=self._interval)
        try:
            while not self._stop_event.is_set():
                try:
                    start = perf_counter()
                    opps = self._scanner.scan()
                    get_metrics().record_scan(
                        "perp_basis", (perf_counter() - start) * 1000,
                    )
                    self._latest_opportunities = opps
                    self._last_scan_at = datetime.now(timezone.utc)
                    if opps:
                        logger.info(
                            "perp_basis_scan_complete",
                            count=len(opps),
                            top_symbol=opps[0].symbol,
                            top_diff_apr=str(round(opps[0].diff_apr_pct, 2)),
                        )
                except Exception:
                    logger.exception("perp_basis_scan_failed")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._interval,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False
            logger.info("perp_basis_runner_stopped")

    def stop(self) -> None:
        self._stop_event.set()
