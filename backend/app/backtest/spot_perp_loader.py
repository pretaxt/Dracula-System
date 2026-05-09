"""spot_perp 回测历史数据加载器

从 CCXT 拉历史 OHLCV，转换为 ``BasisSnapshot`` 序列。
对一对 (spot, perp) 同步取齐时间戳，用收盘价（close）作为该分钟基差快照。

不缓存到 DB（轻量临时方案）；正式 pipeline 待 D.2.e.2 接 TimescaleDB。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from app.backtest.spot_perp_models import BasisSnapshot
from app.core.logging import get_logger
from app.exchanges.base import ExchangeAdapter
from app.exchanges.models import InstrumentType, Symbol

logger = get_logger(__name__)


def _to_decimal(v) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


async def fetch_basis_history(
    adapter: ExchangeAdapter,
    symbol: Symbol,
    since_ms: int,
    until_ms: int,
    timeframe: str = "1m",
) -> list[BasisSnapshot]:
    """从一个交易所拉同标的的 spot + perp OHLCV，对齐时间戳合成 BasisSnapshot。

    跳过任一边数据缺失的时间点。返回按 timestamp 升序。
    """
    spot_client = getattr(adapter, "_clients", {}).get(InstrumentType.SPOT)
    perp_client = getattr(adapter, "_clients", {}).get(InstrumentType.PERPETUAL)
    if spot_client is None or perp_client is None:
        logger.warning(
            "spot_perp_loader_missing_client",
            exchange=getattr(adapter, "_exchange_id", "?"),
            symbol=str(symbol),
        )
        return []

    spot_sym = f"{symbol.base}/{symbol.quote}"
    perp_sym = f"{symbol.base}/{symbol.quote}:{symbol.quote}"

    spot_rows: dict[int, list] = {}
    perp_rows: dict[int, list] = {}

    # 分页拉（CCXT 单次最多 ~1500 根）
    cursor = since_ms
    while cursor < until_ms:
        try:
            spot_chunk = await spot_client.fetch_ohlcv(
                spot_sym, timeframe, since=cursor, limit=1000,
            )
        except Exception as exc:
            logger.warning(
                "spot_ohlcv_fetch_failed",
                symbol=str(symbol), error=str(exc)[:100],
            )
            break
        if not spot_chunk:
            break
        for row in spot_chunk:
            ts = int(row[0])
            if ts > until_ms:
                break
            spot_rows[ts] = row
        last_ts = int(spot_chunk[-1][0])
        if last_ts <= cursor:
            break
        cursor = last_ts + 60_000  # 1m

    cursor = since_ms
    while cursor < until_ms:
        try:
            perp_chunk = await perp_client.fetch_ohlcv(
                perp_sym, timeframe, since=cursor, limit=1000,
            )
        except Exception as exc:
            logger.warning(
                "perp_ohlcv_fetch_failed",
                symbol=str(symbol), error=str(exc)[:100],
            )
            break
        if not perp_chunk:
            break
        for row in perp_chunk:
            ts = int(row[0])
            if ts > until_ms:
                break
            perp_rows[ts] = row
        last_ts = int(perp_chunk[-1][0])
        if last_ts <= cursor:
            break
        cursor = last_ts + 60_000

    # 合成快照
    out: list[BasisSnapshot] = []
    common = sorted(set(spot_rows.keys()) & set(perp_rows.keys()))
    for ts in common:
        spot_close = _to_decimal(spot_rows[ts][4])
        perp_close = _to_decimal(perp_rows[ts][4])
        if spot_close <= 0 or perp_close <= 0:
            continue
        out.append(
            BasisSnapshot(
                timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                symbol=str(symbol),
                exchange=getattr(adapter, "_exchange_id", "unknown"),
                spot_price=spot_close,
                perp_price=perp_close,
            )
        )
    logger.info(
        "spot_perp_history_loaded",
        symbol=str(symbol),
        exchange=getattr(adapter, "_exchange_id", "?"),
        count=len(out),
        spot_only=len(spot_rows) - len(common),
        perp_only=len(perp_rows) - len(common),
    )
    return out


async def fetch_basis_history_multi(
    adapter: ExchangeAdapter,
    symbols: Iterable[Symbol],
    since_ms: int,
    until_ms: int,
    timeframe: str = "1m",
) -> list[BasisSnapshot]:
    """便捷批量加载。串行避免 rate-limit 压力。"""
    out: list[BasisSnapshot] = []
    for sym in symbols:
        snaps = await fetch_basis_history(
            adapter, sym, since_ms, until_ms, timeframe,
        )
        out.extend(snaps)
    return out
