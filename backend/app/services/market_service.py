"""行情服务 — 批量拉 ticker + funding rate(USDM perp)。"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.exchanges.models import InstrumentType

logger = get_logger(__name__)

DEFAULT_SYMBOLS: list[str] = [
    "BTC", "ETH", "SOL", "BNB", "XRP",
    "DOGE", "AVAX", "LINK", "ARB", "OP",
    "SUI", "HYPE",
]


def _to_dec(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return Decimal("0")


async def get_tickers(
    adapters: dict[str, Any] | None,
    symbols: list[str] | None = None,
    exchange: str = "binance",
) -> list[dict]:
    """批量拉 USDM perp ticker + funding rate."""
    adapters = adapters or {}
    pool = symbols or DEFAULT_SYMBOLS
    adapter = adapters.get(exchange)
    if adapter is None:
        logger.warning("market_adapter_missing", exchange=exchange)
        return []

    clients = getattr(adapter, "_clients", {}) or {}
    client = clients.get(InstrumentType.PERPETUAL)
    if client is None:
        logger.warning("market_perp_client_missing", exchange=exchange)
        return []

    ccxt_symbols = [f"{base}/USDT:USDT" for base in pool]

    tickers_raw, rates_raw = await asyncio.gather(
        _safe_fetch_tickers(adapter, client, ccxt_symbols),
        _safe_fetch_funding_rates(adapter, client, ccxt_symbols),
    )

    out: list[dict] = []
    for ccxt_sym in ccxt_symbols:
        t = tickers_raw.get(ccxt_sym, {}) or {}
        r = rates_raw.get(ccxt_sym, {}) or {}
        if not t and not r:
            continue
        base = ccxt_sym.split("/")[0]
        display = f"{base}/USDT"
        last = _to_dec(t.get("last"))
        change_pct = _to_dec(t.get("percentage"))
        quote_vol = _to_dec(t.get("quoteVolume"))
        funding_rate = _to_dec(r.get("fundingRate"))
        funding_pct = funding_rate * Decimal("100")
        next_ms = int(r.get("fundingTimestamp") or 0)

        out.append({
            "symbol": display,
            "exchange": exchange,
            "last": str(last),
            "change_24h_pct": str(round(change_pct, 4)),
            "volume_24h_usd": str(round(quote_vol, 2)),
            "funding_rate": str(funding_rate),
            "funding_rate_pct": str(round(funding_pct, 6)),
            "next_funding_time_ms": next_ms,
            "ts": int(t.get("timestamp") or time.time() * 1000),
        })

    return out


async def _safe_fetch_tickers(
    adapter: Any, client: Any, ccxt_symbols: list[str]
) -> dict[str, Any]:
    try:
        retry = getattr(adapter, "_call_with_retry", None)
        if retry is not None:
            return await retry(client.fetch_tickers, ccxt_symbols) or {}
        return await client.fetch_tickers(ccxt_symbols) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_tickers_failed", error=str(e))
        return {}


async def _safe_fetch_funding_rates(
    adapter: Any, client: Any, ccxt_symbols: list[str]
) -> dict[str, Any]:
    if not hasattr(client, "fetch_funding_rates"):
        return {}
    try:
        retry = getattr(adapter, "_call_with_retry", None)
        if retry is not None:
            raw = await retry(client.fetch_funding_rates, ccxt_symbols)
        else:
            raw = await client.fetch_funding_rates(ccxt_symbols)
        return raw or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_funding_rates_failed", error=str(e))
        return {}


# ---------------------------------------------------------------------------
# K-line / OHLCV
# ---------------------------------------------------------------------------

_VALID_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"}


async def get_klines(
    adapters: dict[str, Any] | None,
    symbol: str,
    interval: str = "1h",
    limit: int = 100,
    exchange: str = "binance",
) -> list[dict]:
    """USDM perp K 线 (CCXT fetch_ohlcv)。

    返回字段::
      [{"time": int_ms, "open": str, "high": str, "low": str,
        "close": str, "volume": str}]
    """
    if interval not in _VALID_INTERVALS:
        interval = "1h"
    limit = max(1, min(limit, 500))

    adapters = adapters or {}
    adapter = adapters.get(exchange)
    if adapter is None:
        return []

    clients = getattr(adapter, "_clients", {}) or {}
    client = clients.get(InstrumentType.PERPETUAL)
    if client is None:
        return []

    base = symbol.upper().split("/")[0]
    ccxt_symbol = f"{base}/USDT:USDT"

    try:
        retry = getattr(adapter, "_call_with_retry", None)
        if retry is not None:
            raw = await retry(client.fetch_ohlcv, ccxt_symbol, interval, None, limit)
        else:
            raw = await client.fetch_ohlcv(ccxt_symbol, interval, None, limit)
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_ohlcv_failed", symbol=ccxt_symbol, error=str(e))
        return []

    out: list[dict] = []
    for r in (raw or []):
        if len(r) < 6:
            continue
        out.append({
            "time": int(r[0]),
            "open": str(_to_dec(r[1])),
            "high": str(_to_dec(r[2])),
            "low": str(_to_dec(r[3])),
            "close": str(_to_dec(r[4])),
            "volume": str(_to_dec(r[5])),
        })
    return out
