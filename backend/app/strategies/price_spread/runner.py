"""#03 price-spread runner — 周期扫描 + 暴露最新机会。

scanner 同步无 IO（数据全在 hub），runner 负责：
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
from app.strategies.price_spread.scanner import (
    PriceSpreadOpportunity,
    PriceSpreadScanner,
)

logger = get_logger(__name__)


class PriceSpreadRunner:
    def __init__(
        self,
        scanner: PriceSpreadScanner,
        scan_interval_seconds: float = 30.0,
    ) -> None:
        self._scanner = scanner
        self._interval = scan_interval_seconds
        self._latest_opportunities: list[PriceSpreadOpportunity] = []
        self._last_scan_at: datetime | None = None
        self._stop_event = asyncio.Event()
        self._running = False

    @property
    def latest_opportunities(self) -> list[PriceSpreadOpportunity]:
        return self._latest_opportunities

    @property
    def last_scan_at(self) -> datetime | None:
        return self._last_scan_at

    @property
    def is_running(self) -> bool:
        return self._running

    async def run_forever(self) -> None:
        self._running = True
        logger.info("price_spread_runner_started", interval=self._interval)
        try:
            while not self._stop_event.is_set():
                try:
                    start = perf_counter()
                    opps = self._scanner.scan()
                    get_metrics().record_scan(
                        "price_spread", (perf_counter() - start) * 1000,
                    )
                    self._latest_opportunities = opps
                    self._last_scan_at = datetime.now(timezone.utc)
                    if opps:
                        logger.info(
                            "price_spread_scan_complete",
                            count=len(opps),
                            top_symbol=opps[0].symbol,
                            top_spread_pct=str(round(opps[0].spread_pct, 3)),
                            top_pair=f"{opps[0].long_exchange}→{opps[0].short_exchange}",
                        )
                    else:
                        logger.debug("price_spread_scan_no_opportunities")
                except Exception:
                    logger.exception("price_spread_scan_failed")
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._interval,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False
            logger.info("price_spread_runner_stopped")

    def stop(self) -> None:
        self._stop_event.set()
