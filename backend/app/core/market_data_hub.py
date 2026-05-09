"""Market Data Hub — 跨策略共享行情缓存

设计目标
========
N 个策略并发扫描时，避免每个策略独立向同一 exchange 拉同样的 tickers / funding
rates。Hub 后台单一进程拉一次，所有策略读缓存。

每个 (exchange, data_type) 一个 fetcher 协程：
    BulkTickerFetcher   ─→ 每 30s ccxt fetch_tickers() per exchange (spot + perp)
    BulkFundingFetcher  ─→ 每 60s ccxt fetch_funding_rates() per exchange

策略读取
--------
    hub = get_market_data_hub()
    tickers = hub.get_tickers("binance", InstrumentType.PERPETUAL)
    funding = hub.get_funding_rate("binance", Symbol("BTC", "USDT"))

返回的数据带 ``fetched_at`` 时间戳；调用方自己判断 staleness。
Hub 不做插值或外推，只是被动缓存。

单例 + 进程内：一个 uvicorn worker 共享同一份 hub。多 worker 时建议
未来切换到 Redis backed 实现（接口不变）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.core.metrics import get_metrics
from app.exchanges.models import FundingRate, InstrumentType, Symbol

logger = get_logger(__name__)


_DEFAULT_TICKER_INTERVAL_SECONDS = 30.0
_DEFAULT_FUNDING_INTERVAL_SECONDS = 60.0
_DEFAULT_STALE_TICKER_SECONDS = 90.0
_DEFAULT_STALE_FUNDING_SECONDS = 180.0


# ---------------------------------------------------------------------------
# 数据条目（含 fetched_at 时间戳）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickerEntry:
    """单个 (exchange, instrument, symbol) ticker 快照。"""
    raw: dict          # CCXT raw ticker dict
    fetched_at: datetime

    @property
    def bid(self) -> Decimal | None:
        v = self.raw.get("bid")
        return Decimal(str(v)) if v is not None else None

    @property
    def ask(self) -> Decimal | None:
        v = self.raw.get("ask")
        return Decimal(str(v)) if v is not None else None

    @property
    def last(self) -> Decimal | None:
        v = self.raw.get("last") or self.raw.get("close")
        return Decimal(str(v)) if v is not None else None

    def is_stale(self, max_age_seconds: float) -> bool:
        return (datetime.now(timezone.utc) - self.fetched_at).total_seconds() > max_age_seconds


@dataclass(frozen=True)
class FundingRateEntry:
    rate: FundingRate
    fetched_at: datetime

    def is_stale(self, max_age_seconds: float) -> bool:
        return (datetime.now(timezone.utc) - self.fetched_at).total_seconds() > max_age_seconds


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------


@dataclass
class _ExchangeCache:
    # tickers: {(instrument_type, symbol_str): TickerEntry}
    tickers: dict[tuple[str, str], TickerEntry] = field(default_factory=dict)
    # funding rates: {symbol_str: FundingRateEntry}
    funding_rates: dict[str, FundingRateEntry] = field(default_factory=dict)
    last_ticker_fetch_at: datetime | None = None
    last_funding_fetch_at: datetime | None = None
    last_ticker_count: int = 0
    last_funding_count: int = 0
    consecutive_failures: int = 0


class MarketDataHub:
    """跨策略共享的行情缓存。"""

    def __init__(
        self,
        adapters: dict,
        ticker_interval_seconds: float = _DEFAULT_TICKER_INTERVAL_SECONDS,
        funding_interval_seconds: float = _DEFAULT_FUNDING_INTERVAL_SECONDS,
    ) -> None:
        self._adapters = adapters
        self._ticker_interval = ticker_interval_seconds
        self._funding_interval = funding_interval_seconds
        self._cache: dict[str, _ExchangeCache] = {
            ex: _ExchangeCache() for ex in adapters
        }
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动每个 exchange × 每个数据类型 的 fetcher task。"""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        for ex in self._adapters:
            self._tasks.append(asyncio.create_task(
                self._ticker_loop(ex), name=f"hub_ticker_{ex}",
            ))
            self._tasks.append(asyncio.create_task(
                self._funding_loop(ex), name=f"hub_funding_{ex}",
            ))
        logger.info(
            "market_data_hub_started",
            exchanges=list(self._adapters.keys()),
            ticker_s=self._ticker_interval,
            funding_s=self._funding_interval,
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self._running = False
        logger.info("market_data_hub_stopped")

    # ------------------------------------------------------------------
    # 公共读取 API
    # ------------------------------------------------------------------

    def get_ticker(
        self, exchange: str, instrument: InstrumentType, symbol: Symbol,
        max_age_seconds: float = _DEFAULT_STALE_TICKER_SECONDS,
    ) -> TickerEntry | None:
        cache = self._cache.get(exchange.lower())
        if cache is None:
            return None
        entry = cache.tickers.get((instrument.value, str(symbol)))
        if entry is None or entry.is_stale(max_age_seconds):
            return None
        return entry

    def get_tickers(
        self, exchange: str, instrument: InstrumentType,
        max_age_seconds: float = _DEFAULT_STALE_TICKER_SECONDS,
    ) -> dict[str, TickerEntry]:
        """返回 {symbol_str: TickerEntry}。陈旧条目被过滤。"""
        cache = self._cache.get(exchange.lower())
        if cache is None:
            return {}
        out: dict[str, TickerEntry] = {}
        for (inst, sym_str), entry in cache.tickers.items():
            if inst == instrument.value and not entry.is_stale(max_age_seconds):
                out[sym_str] = entry
        return out

    def get_funding_rate(
        self, exchange: str, symbol: Symbol,
        max_age_seconds: float = _DEFAULT_STALE_FUNDING_SECONDS,
    ) -> FundingRateEntry | None:
        cache = self._cache.get(exchange.lower())
        if cache is None:
            return None
        entry = cache.funding_rates.get(str(symbol))
        if entry is None or entry.is_stale(max_age_seconds):
            return None
        return entry

    def health(self) -> dict[str, dict[str, Any]]:
        """运维监控用。返回各 exchange 的 fetch 状态。"""
        out: dict[str, dict] = {}
        now = datetime.now(timezone.utc)
        for ex, c in self._cache.items():
            tk_age = (now - c.last_ticker_fetch_at).total_seconds() if c.last_ticker_fetch_at else None
            fd_age = (now - c.last_funding_fetch_at).total_seconds() if c.last_funding_fetch_at else None
            out[ex] = {
                "ticker_count": c.last_ticker_count,
                "funding_count": c.last_funding_count,
                "ticker_age_s": round(tk_age, 1) if tk_age is not None else None,
                "funding_age_s": round(fd_age, 1) if fd_age is not None else None,
                "consecutive_failures": c.consecutive_failures,
            }
        return out

    # ------------------------------------------------------------------
    # 后台 fetch 循环
    # ------------------------------------------------------------------

    async def _ticker_loop(self, exchange: str) -> None:
        while not self._stop_event.is_set():
            try:
                await self._fetch_tickers_once(exchange)
            except Exception:
                logger.exception("hub_ticker_loop_error", exchange=exchange)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._ticker_interval,
                )
            except asyncio.TimeoutError:
                pass

    async def _funding_loop(self, exchange: str) -> None:
        while not self._stop_event.is_set():
            try:
                await self._fetch_funding_once(exchange)
            except Exception:
                logger.exception("hub_funding_loop_error", exchange=exchange)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._funding_interval,
                )
            except asyncio.TimeoutError:
                pass

    async def _fetch_tickers_once(self, exchange: str) -> None:
        adapter = self._adapters.get(exchange)
        if adapter is None:
            return
        cache = self._cache[exchange]
        clients = getattr(adapter, "_clients", {}) or {}
        now = datetime.now(timezone.utc)
        total_fetched = 0
        for inst, client in clients.items():
            if client is None or not hasattr(client, "fetch_tickers"):
                continue
            try:
                raw = await asyncio.wait_for(client.fetch_tickers(), timeout=15.0)
            except Exception as exc:
                cache.consecutive_failures += 1
                get_metrics().record_ccxt(exchange, ok=False)
                logger.debug(
                    "hub_fetch_tickers_failed",
                    exchange=exchange, instrument=inst.value,
                    error=str(exc)[:120],
                )
                continue
            if not isinstance(raw, dict):
                continue
            cache.consecutive_failures = 0
            get_metrics().record_ccxt(exchange, ok=True)
            for sym_str, t in raw.items():
                if not isinstance(t, dict):
                    continue
                cache.tickers[(inst.value, sym_str)] = TickerEntry(
                    raw=t, fetched_at=now,
                )
                total_fetched += 1
        cache.last_ticker_fetch_at = now
        cache.last_ticker_count = total_fetched
        if total_fetched > 0:
            logger.debug(
                "hub_tickers_refreshed",
                exchange=exchange, count=total_fetched,
            )

    async def _fetch_funding_once(self, exchange: str) -> None:
        adapter = self._adapters.get(exchange)
        if adapter is None:
            return
        cache = self._cache[exchange]
        clients = getattr(adapter, "_clients", {}) or {}
        perp_client = clients.get(InstrumentType.PERPETUAL)
        if perp_client is None or not hasattr(perp_client, "fetch_funding_rates"):
            # 跳过：CCXT 部分交易所只支持 per-symbol fetch_funding_rate（这种成本太高，
            # 不在 hub 缓存全量；策略可降级到自己拉单标的）
            return
        now = datetime.now(timezone.utc)
        try:
            raw = await asyncio.wait_for(
                perp_client.fetch_funding_rates(), timeout=15.0,
            )
        except Exception as exc:
            cache.consecutive_failures += 1
            get_metrics().record_ccxt(exchange, ok=False)
            logger.debug(
                "hub_fetch_funding_failed",
                exchange=exchange, error=str(exc)[:120],
            )
            return
        if not isinstance(raw, dict):
            return
        cache.consecutive_failures = 0
        get_metrics().record_ccxt(exchange, ok=True)
        from app.exchanges.cex.funding_interval import infer_funding_interval_hours  # noqa: PLC0415
        count = 0
        for sym_str, r in raw.items():
            if not isinstance(r, dict):
                continue
            try:
                base, _, quote = sym_str.partition("/")
                # CCXT 永续 symbol 格式 "BTC/USDT:USDT" — 取 quote 前的部分
                if ":" in quote:
                    quote = quote.split(":")[0]
                base = base.strip().upper()
                quote = (quote or "USDT").upper()
                if not base:
                    continue
                fr = FundingRate(
                    symbol=Symbol(base, quote),
                    exchange=exchange,
                    rate=Decimal(str(r.get("fundingRate") or 0)),
                    next_funding_time=int(r.get("fundingTimestamp") or 0),
                    funding_interval_hours=infer_funding_interval_hours(r),
                )
                cache.funding_rates[f"{base}/{quote}"] = FundingRateEntry(
                    rate=fr, fetched_at=now,
                )
                count += 1
            except Exception:
                continue
        cache.last_funding_fetch_at = now
        cache.last_funding_count = count
        if count > 0:
            logger.debug(
                "hub_funding_refreshed", exchange=exchange, count=count,
            )


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------


_HUB: MarketDataHub | None = None


def get_market_data_hub() -> MarketDataHub | None:
    return _HUB


def set_market_data_hub(hub: MarketDataHub | None) -> None:
    global _HUB
    _HUB = hub
