"""真实账户余额聚合服务

通过已初始化的交易所 adapter 拉取 spot + USDM perp 钱包，
将所有持仓换算成 USD（USDT/USDC/BUSD/FDUSD/DAI 1:1，其他币用现货 ticker），
返回总资产估值。

设计：
- 60 秒 TTL 内存缓存 — 避免 dashboard 每次请求都打 Binance（rate limit 友好）
- 任意一步失败返回 None — 调用方应 fallback 到 settings.initial_capital_usd
- 异步并发拉 ticker（持仓 < 20 种币就够快）
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.exchanges.models import InstrumentType, Symbol

logger = get_logger(__name__)

_STABLECOINS = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "DAI", "TUSD"})
_CACHE_TTL_SECONDS = 60.0

# 模块级缓存（asyncio 单线程下安全）
_cache_value: Decimal | None = None
_cache_expires_at: float = 0.0
_cache_lock = asyncio.Lock()

# 每交易所缓存
_per_exchange_cache: dict[str, Decimal] | None = None
_per_exchange_expires_at: float = 0.0
_per_exchange_lock = asyncio.Lock()


async def _ticker_usd(adapter: Any, asset: str) -> Decimal | None:
    """单个非稳定币 → USDT 报价，拉取失败返回 None。"""
    try:
        ticker = await adapter.fetch_ticker(Symbol(asset, "USDT"))
        if ticker and ticker.last and ticker.last > 0:
            return ticker.last
    except Exception as exc:
        logger.debug("ticker_fetch_skipped", asset=asset, error=str(exc)[:120])
    return None


async def _spot_usd_total(adapter: Any) -> Decimal:
    """汇总 spot 钱包所有非零持仓的 USD 等值。"""
    bal = await adapter.fetch_balance()
    total = Decimal("0")
    nonstable_assets: list[tuple[str, Decimal]] = []
    for entry in bal.entries:
        amount = entry.free + entry.locked
        if amount <= 0:
            continue
        if entry.asset in _STABLECOINS:
            total += amount
        else:
            nonstable_assets.append((entry.asset, amount))

    if nonstable_assets:
        prices = await asyncio.gather(
            *(_ticker_usd(adapter, asset) for asset, _ in nonstable_assets),
            return_exceptions=False,
        )
        for (asset, amount), price in zip(nonstable_assets, prices):
            if price is not None:
                total += amount * price
    return total


async def _perp_usdt_total(adapter: Any) -> Decimal:
    """USDM perp 钱包 USDT 余额（含 unrealized PnL，CCXT total 已合）。"""
    try:
        client = adapter._clients.get(InstrumentType.PERPETUAL)
        if client is None:
            return Decimal("0")
        raw = await client.fetch_balance()
        usdt_total = (raw.get("total") or {}).get("USDT") or 0
        return Decimal(str(usdt_total))
    except Exception as exc:
        logger.debug("perp_balance_skipped", error=str(exc)[:120])
        return Decimal("0")


_PER_EXCHANGE_TIMEOUT_S = 8.0  # 单交易所余额拉取硬超时（避免 OKX 慢导致 dashboard 整体卡）


async def get_per_exchange_equity(adapters: dict[str, Any]) -> dict[str, Decimal]:
    """每个交易所的 USD 等值汇总（spot + USDM perp），60s 缓存 + 8s 单交易所超时。

    任意交易所拉取超时/失败时，该交易所返回 0（或上次成功的缓存值）。
    """
    global _per_exchange_cache, _per_exchange_expires_at

    now = time.monotonic()
    if _per_exchange_cache is not None and now < _per_exchange_expires_at:
        return _per_exchange_cache

    async with _per_exchange_lock:
        if _per_exchange_cache is not None and time.monotonic() < _per_exchange_expires_at:
            return _per_exchange_cache

        out: dict[str, Decimal] = {}
        items = list((adapters or {}).items())

        async def _fetch_one(ex_name: str, adapter: Any) -> Decimal:
            if not getattr(adapter, "_api_key", ""):
                return Decimal("0")
            try:
                # 8s 硬超时 — 单交易所卡住不影响整体响应
                async def _both():
                    s, p = await asyncio.gather(
                        _spot_usd_total(adapter), _perp_usdt_total(adapter),
                    )
                    return s + p
                return await asyncio.wait_for(_both(), timeout=_PER_EXCHANGE_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("per_exchange_equity_timeout", exchange=ex_name,
                               timeout_s=_PER_EXCHANGE_TIMEOUT_S)
                # 用上次成功值兜底；没有则 0
                return (_per_exchange_cache or {}).get(ex_name, Decimal("0"))
            except Exception as exc:
                logger.warning("per_exchange_equity_failed",
                               exchange=ex_name, error=str(exc)[:200])
                return (_per_exchange_cache or {}).get(ex_name, Decimal("0"))

        results = await asyncio.gather(
            *[_fetch_one(n, a) for n, a in items],
            return_exceptions=False,
        )
        for (n, _), r in zip(items, results):
            out[n] = r

        _per_exchange_cache = out
        _per_exchange_expires_at = time.monotonic() + _CACHE_TTL_SECONDS
        return out


async def get_total_equity_usd(adapters: dict[str, Any]) -> Decimal | None:
    """聚合 Binance 真实账户 USD 等值（spot + USDM perp）。

    返回 None 表示拉取失败（调用方应 fallback）。
    缓存 60s 避免频繁打 API。
    """
    global _cache_value, _cache_expires_at

    now = time.monotonic()
    if _cache_value is not None and now < _cache_expires_at:
        return _cache_value

    async with _cache_lock:
        # 双检：可能其他协程已刷新
        if _cache_value is not None and time.monotonic() < _cache_expires_at:
            return _cache_value

        binance = adapters.get("binance")
        if binance is None:
            logger.debug("real_balance_no_binance_adapter")
            return None

        try:
            spot_total, perp_total = await asyncio.gather(
                _spot_usd_total(binance),
                _perp_usdt_total(binance),
            )
            total = spot_total + perp_total
            _cache_value = total
            _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS
            logger.info(
                "real_balance_fetched",
                spot_usd=str(round(spot_total, 4)),
                perp_usdt=str(round(perp_total, 4)),
                total_usd=str(round(total, 4)),
            )
            return total
        except Exception as exc:
            logger.warning("real_balance_fetch_failed", error=str(exc)[:200])
            return None
