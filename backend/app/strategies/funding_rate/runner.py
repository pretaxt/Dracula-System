"""周期性资金费率扫描运行器

每隔 ``scan_interval_seconds`` 执行一次 :class:`FundingRateScanner`，
将结果持久化到 TimescaleDB，并通过 Redis 发布到
``dracula:funding_rate:opportunities`` 频道。

用法（FastAPI lifespan）::

    runner = FundingRateRunner(adapters=..., symbols=..., config=...)
    asyncio.create_task(runner.run_forever())
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.core.redis_client import publish
from app.exchanges.base import ExchangeAdapter
from app.exchanges.models import Symbol
from app.models.funding_rate import FundingRateRecord
from app.strategies.funding_rate.scanner import (
    FundingRateOpportunity,
    FundingRateScanner,
    ScannerConfig,
)

logger = get_logger(__name__)

# Redis pub/sub 频道
OPPORTUNITIES_CHANNEL = "dracula:funding_rate:opportunities"


class FundingRateRunner:
    """封装扫描循环、DB 写入与 Redis 发布。"""

    def __init__(
        self,
        adapters: dict[str, ExchangeAdapter],
        symbols: list[Symbol],
        config: ScannerConfig,
        scan_interval_seconds: float = 60.0,
    ) -> None:
        self._scanner = FundingRateScanner(
            adapters=adapters,
            symbols=symbols,
            config=config,
        )
        self._interval = scan_interval_seconds
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """无限循环，直到外部取消协程。"""
        self._running = True
        logger.info("funding_rate_runner_started", interval=self._interval)
        while self._running:
            start = asyncio.get_event_loop().time()
            await self._tick()
            elapsed = asyncio.get_event_loop().time() - start
            sleep_for = max(0.0, self._interval - elapsed)
            await asyncio.sleep(sleep_for)

    async def stop(self) -> None:
        """优雅停止（下一次 sleep 结束后退出）。"""
        self._running = False

    async def run_once(self) -> list[FundingRateOpportunity]:
        """执行单次扫描（测试 / 脚本友好）。"""
        return await self._tick()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _tick(self) -> list[FundingRateOpportunity]:
        scanned_at = datetime.now(UTC)
        try:
            opportunities = await self._scanner.scan()
        except Exception:
            logger.exception("funding_rate_scan_failed")
            return []

        logger.info(
            "funding_rate_scan_complete",
            opportunities=len(opportunities),
            scanned_at=scanned_at.isoformat(),
        )

        if opportunities:
            await asyncio.gather(
                self._persist(opportunities, scanned_at),
                self._publish(opportunities, scanned_at),
                return_exceptions=True,
            )

        return opportunities

    async def _persist(
        self,
        opportunities: list[FundingRateOpportunity],
        scanned_at: datetime,
    ) -> None:
        """批量 upsert 到 TimescaleDB。"""
        # local import 允许测试中注入 mock
        from app.core.database import get_session  # noqa: PLC0415

        records = [
            FundingRateRecord(
                time=scanned_at,
                exchange=opp.exchange,
                symbol=str(opp.symbol),
                instrument_type="PERPETUAL",
                funding_rate=opp.funding_rate.rate,
                apr_pct=opp.apr_pct,
                next_funding_time=(
                    datetime.fromtimestamp(
                        opp.funding_rate.next_funding_time / 1000, tz=UTC
                    )
                    if opp.funding_rate.next_funding_time
                    else None
                ),
                funding_interval_hours=opp.funding_rate.funding_interval_hours,
            )
            for opp in opportunities
        ]

        try:
            async with get_session() as session:
                for record in records:
                    await session.merge(record)
            logger.info("funding_rate_persisted", count=len(records))
        except Exception:
            logger.exception("funding_rate_persist_failed")

    async def _publish(
        self,
        opportunities: list[FundingRateOpportunity],
        scanned_at: datetime,
    ) -> None:
        """将机会列表序列化后发布到 Redis 频道。"""
        payload = json.dumps(
            {
                "scanned_at": scanned_at.isoformat(),
                "opportunities": [
                    {
                        "exchange": opp.exchange,
                        "symbol": str(opp.symbol),
                        "apr_pct": str(opp.apr_pct),
                        "funding_rate": str(opp.funding_rate.rate),
                        "funding_interval_hours": opp.funding_rate.funding_interval_hours,
                    }
                    for opp in opportunities
                ],
            }
        )
        try:
            receivers = await publish(OPPORTUNITIES_CHANNEL, payload)
            logger.info(
                "funding_rate_published",
                channel=OPPORTUNITIES_CHANNEL,
                receivers=receivers,
            )
        except Exception:
            logger.exception("funding_rate_publish_failed")
