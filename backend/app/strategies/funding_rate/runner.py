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
from datetime import UTC, datetime, timedelta

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

    # 缓存 24h 没更新的 schedule 条目即视为过期，下个 tick 清掉防止陈旧 trigger
    _SCHEDULE_GC_HOURS = 24

    def __init__(
        self,
        adapters: dict[str, ExchangeAdapter],
        symbols: list[Symbol],
        config: ScannerConfig,
        scan_interval_seconds: float = 60.0,
        window_only_minutes: float = 0.0,
    ) -> None:
        """funding-rate 扫描循环。

        Parameters
        ----------
        window_only_minutes:
            仅在任意已知标的的 funding 结算前 N 分钟内才执行扫描，
            0 = 禁用（24h 不停扫，旧行为）。
            用每标的 ``next_funding_time`` 真实判断，不假设固定周期。
            冷启动（cache 空）时强制扫描一次以填 cache。
        """
        self._scanner = FundingRateScanner(
            adapters=adapters,
            symbols=symbols,
            config=config,
        )
        self._interval = scan_interval_seconds
        self._window_minutes = float(window_only_minutes or 0)
        self._running = False
        # 最近一次扫描结果 — 供 API 暴露给前端实时机会表
        self._latest_opportunities: list[FundingRateOpportunity] = []
        self._last_scan_at: datetime | None = None
        # Schedule cache: (exchange, symbol_str) → {next_funding_ms, interval_hours, updated_at}
        # 用每标的真实 funding 时间 gate 扫描，自动适配 1h / 4h / 8h 周期
        self._schedule_cache: dict[tuple[str, str], dict] = {}

    @property
    def latest_opportunities(self) -> list[FundingRateOpportunity]:
        return self._latest_opportunities

    @property
    def last_scan_at(self) -> datetime | None:
        return self._last_scan_at

    @property
    def is_running(self) -> bool:
        return self._running

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
        from time import perf_counter  # noqa: PLC0415
        from app.core.metrics import get_metrics  # noqa: PLC0415
        scanned_at = datetime.now(UTC)

        # window-gate：先 GC 过期 cache，再 advance 过期 next_funding，再判断窗口
        self._gc_stale_schedule(scanned_at)
        self._advance_schedule(scanned_at)
        if not self._is_in_funding_window(scanned_at):
            logger.debug(
                "funding_rate_scan_skipped_outside_window",
                window_min=self._window_minutes,
                cache_size=len(self._schedule_cache),
            )
            return self._latest_opportunities  # 用上次缓存

        scan_start = perf_counter()
        try:
            opportunities = await self._scanner.scan()
        except Exception:
            logger.exception("funding_rate_scan_failed")
            return []
        get_metrics().record_scan(
            "funding_rate", (perf_counter() - scan_start) * 1000,
        )

        # 缓存供 API 实时读取
        self._latest_opportunities = opportunities
        self._last_scan_at = scanned_at
        # 用本次扫到的真实 next_funding_time 更新 schedule cache
        self._update_schedule(opportunities, scanned_at)

        # 现算 passes_entry（基于当前 cfg.min_apr_pct，不读 stored 字段）
        min_apr = self._scanner._config.min_apr_pct
        passing = sum(1 for o in opportunities if o.apr_pct >= min_apr)
        logger.info(
            "funding_rate_scan_complete",
            opportunities=len(opportunities),
            passes_entry=passing,
            scanned_at=scanned_at.isoformat(),
        )

        if opportunities:
            await asyncio.gather(
                self._persist(opportunities, scanned_at),
                self._publish(opportunities, scanned_at),
                return_exceptions=True,
            )

        return opportunities

    # ------------------------------------------------------------------
    # Schedule cache — 用每标的真实 funding 时间 gate 扫描（取消 8h 假设）
    # ------------------------------------------------------------------

    def _update_schedule(
        self,
        opportunities: list[FundingRateOpportunity],
        now: datetime,
    ) -> None:
        """从扫描结果更新 cache。覆盖式写入，updated_at 用于 GC。"""
        for opp in opportunities:
            key = (opp.exchange, str(opp.symbol))
            self._schedule_cache[key] = {
                "next_funding_ms": int(opp.funding_rate.next_funding_time or 0),
                "interval_hours": int(opp.funding_rate.funding_interval_hours or 8),
                "updated_at": now,
            }

    def _advance_schedule(self, now: datetime) -> None:
        """auto-advance 过期的 next_funding（now > next_funding 时 += interval）。

        无需 fresh scan 也能正确判断"下个窗口何时到"，让标的 schedule 自洽推进。
        """
        now_ms = int(now.timestamp() * 1000)
        for sched in self._schedule_cache.values():
            interval_ms = int(sched.get("interval_hours") or 8) * 3600 * 1000
            if interval_ms <= 0:
                continue
            while sched["next_funding_ms"] > 0 and sched["next_funding_ms"] <= now_ms:
                sched["next_funding_ms"] += interval_ms

    def _gc_stale_schedule(self, now: datetime) -> None:
        """清理 24h 没更新的 cache 条目，防止下架/失联标的陈旧 trigger 扫描。"""
        cutoff = now - timedelta(hours=self._SCHEDULE_GC_HOURS)
        stale = [
            k for k, v in self._schedule_cache.items()
            if v.get("updated_at") and v["updated_at"] < cutoff
        ]
        for k in stale:
            self._schedule_cache.pop(k, None)
        if stale:
            logger.info(
                "funding_rate_schedule_cache_gc",
                removed=len(stale),
                remaining=len(self._schedule_cache),
            )

    def _is_in_funding_window(self, now: datetime) -> bool:
        """当前是否在任一已知标的的 funding 窗口内。

        - ``window_minutes <= 0`` → 禁用 gate，永远 True（旧行为）
        - cache 空（冷启动）→ True，第一次扫描必发以填充 cache
        - 否则：任意标的 ``next_funding_ms - now <= window_minutes`` 即返 True
        """
        if self._window_minutes <= 0:
            return True
        if not self._schedule_cache:
            return True
        now_ms = int(now.timestamp() * 1000)
        window_ms = int(self._window_minutes * 60 * 1000)
        return any(
            (sched["next_funding_ms"] - now_ms) <= window_ms
            for sched in self._schedule_cache.values()
            if sched.get("next_funding_ms", 0) > 0
        )

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
