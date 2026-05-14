"""#02 跨所 funding 差套利回测数据加载器

从 CCXT 历史 API 拉每个 (exchange, symbol) 的 funding rate 序列。
返回扁平 list[PerpFundingSnapshot]，按 timestamp 升序排序。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from app.backtest.perp_basis_models import PerpFundingSnapshot
from app.core.logging import get_logger

logger = get_logger(__name__)


# 每个 funding history 单次最多 1000 条；ccxt 各家差异
_PAGE_LIMIT = 1000

# 默认 funding interval（小时），少数 exchange 4h/1h 时由数据自动覆盖
_DEFAULT_INTERVAL_HOURS = 8


def _to_dec(v) -> Decimal:
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


async def load_funding_history(
    adapters: dict,
    symbols: Iterable[str],
    days: int = 30,
    page_limit: int = _PAGE_LIMIT,
    min_volume_24h_usd: Decimal | None = None,
) -> list[PerpFundingSnapshot]:
    """为 adapters 中每个 exchange × 每个 symbol 拉 days 天 funding history。

    Parameters
    ----------
    adapters : dict[exchange_name → ExchangeAdapter]
    symbols : iterable of "BASE/QUOTE" strings
    days : 拉取天数

    Returns
    -------
    list[PerpFundingSnapshot] 时间升序，跨所 + 跨 symbol 全合并
    """
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - days * 24 * 3600 * 1000

    out: list[PerpFundingSnapshot] = []

    async def fetch_one(ex_name: str, adapter, sym_str: str) -> list[PerpFundingSnapshot]:
        """对一个 (exchange, symbol) 分页拉 funding history。"""
        from app.exchanges.models import InstrumentType  # noqa: PLC0415
        try:
            client = adapter._clients.get(InstrumentType.PERPETUAL)
            if client is None:
                return []
            # ccxt unified symbol "BASE/QUOTE:QUOTE" for perpetual
            base, _, quote = sym_str.partition("/")
            ccxt_sym = f"{base}/{quote}:{quote}"
            cursor = start_ms
            all_raw: list[dict] = []
            while cursor < end_ms:
                try:
                    page = await client.fetch_funding_rate_history(
                        ccxt_sym, since=cursor, limit=page_limit,
                    )
                except Exception as e:
                    logger.debug(
                        "funding_history_page_failed",
                        exchange=ex_name, symbol=sym_str, error=str(e),
                    )
                    break
                if not page:
                    break
                all_raw.extend(page)
                last_ts = page[-1].get("timestamp") or page[-1].get("fundingTimestamp")
                if not last_ts or last_ts <= cursor:
                    break
                cursor = last_ts + 1  # 下一页起点

            snaps: list[PerpFundingSnapshot] = []
            for r in all_raw:
                ts = r.get("timestamp") or r.get("fundingTimestamp") or 0
                if not ts:
                    continue
                rate = _to_dec(r.get("fundingRate"))
                # 价格：尝试从 raw 取 mark / markPrice，否则 0
                info = r.get("info") or {}
                price = (_to_dec(r.get("markPrice")) or _to_dec(r.get("mark")) or _to_dec(r.get("indexPrice"))
                        or _to_dec(info.get("markPrice")) or _to_dec(info.get("indexPrice")) or _to_dec(info.get("p")))
                snaps.append(PerpFundingSnapshot(
                    timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                    symbol=sym_str,
                    exchange=ex_name,
                    funding_rate=rate,
                    funding_interval_hours=_infer_interval(r),
                    perp_price=price,
                ))
            return snaps
        except Exception as e:
            logger.warning(
                "funding_history_fetch_failed",
                exchange=ex_name, symbol=sym_str, error=str(e),
            )
            return []

    tasks = []
    for ex_name, adapter in adapters.items():
        for sym in symbols:
            tasks.append(fetch_one(ex_name, adapter, sym))

    results = await asyncio.gather(*tasks, return_exceptions=False)
    for snaps in results:
        out.extend(snaps)

    # min_volume 过滤（防脏数据小币种 funding 异常）
    if min_volume_24h_usd is not None and min_volume_24h_usd > 0:
        valid_pairs = await _fetch_volume_filter(
            adapters, list(symbols), Decimal(str(min_volume_24h_usd)),
        )
        before = len(out)
        out = [s for s in out if (s.exchange, s.symbol) in valid_pairs]
        logger.info(
            "perp_basis_min_volume_filter",
            min_vol=str(min_volume_24h_usd),
            kept=len(out), filtered=before - len(out),
        )

    out.sort(key=lambda s: s.timestamp)
    logger.info(
        "perp_basis_funding_history_loaded",
        snapshots=len(out),
        exchanges=list(adapters.keys()),
        symbols=list(symbols),
        days=days,
    )
    return out


async def _fetch_volume_filter(
    adapters: dict,
    symbols: list[str],
    min_volume_usd: Decimal,
) -> set[tuple[str, str]]:
    """对每个 (exchange, symbol) 拉 perp ticker 看 24h quoteVolume，过滤低于阈值的。

    Returns set of (exchange, symbol) tuples that pass volume filter.
    """
    from app.exchanges.models import InstrumentType  # noqa: PLC0415
    valid: set[tuple[str, str]] = set()

    async def check_one(ex_name: str, adapter, sym: str):
        try:
            client = adapter._clients.get(InstrumentType.PERPETUAL)
            if client is None:
                return
            base, _, quote = sym.partition("/")
            ccxt_sym = f"{base}/{quote}:{quote}"
            t = await asyncio.wait_for(client.fetch_ticker(ccxt_sym), timeout=5.0)
            vol = Decimal(str(t.get("quoteVolume") or 0))
            if vol >= min_volume_usd:
                valid.add((ex_name, sym))
        except Exception:
            pass

    tasks = [
        check_one(n, a, s)
        for n, a in adapters.items()
        for s in symbols
    ]
    await asyncio.gather(*tasks, return_exceptions=False)
    return valid


def _infer_interval(raw: dict) -> int:
    """从 raw funding history 条目推断 funding interval（小时）。

    优先：fundingIntervalHours / interval / fallback 8h。
    """
    for key in ("fundingIntervalHours", "interval_hours", "fundingInterval"):
        v = raw.get(key)
        if v:
            try:
                hours = int(v)
                # 部分 exchange 用 ms
                if hours > 24:
                    hours = hours // (3600 * 1000)
                if hours > 0 and hours <= 24:
                    return hours
            except Exception:
                pass
    return _DEFAULT_INTERVAL_HOURS
