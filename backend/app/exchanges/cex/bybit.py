"""BybitAdapter — Bybit USDT linear perpetual + spot 适配器。"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

import ccxt.async_support as ccxt

from app.core.logging import get_logger
from app.exchanges.cex.ccxt_base import CCXTAdapter
from app.exchanges.cex.funding_interval import infer_funding_interval_hours
from app.exchanges.models import FundingRate, InstrumentType, Symbol

logger = get_logger(__name__)

_DEFAULT_FUNDING_INTERVAL_HOURS = 8


class BybitAdapter(CCXTAdapter):
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False,
    ) -> None:
        super().__init__(
            exchange_id="bybit",
            api_key=api_key,
            api_secret=api_secret,
            max_rpm=500,
        )
        config: dict = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }
        perp_client = ccxt.bybit({
            **config,
            "options": {"defaultType": "linear"},  # USDT linear perpetual
        })
        spot_client = ccxt.bybit({
            **config,
            "options": {"defaultType": "spot"},
        })
        if testnet:
            perp_client.set_sandbox_mode(True)
            spot_client.set_sandbox_mode(True)
            logger.info("bybit_testnet_mode")
        self._clients = {
            InstrumentType.PERPETUAL: perp_client,
            InstrumentType.SPOT: spot_client,
        }

    async def top_up_perp_margin(self, amount: "Decimal") -> None:
        # Bybit Unified Trading Account 共享余额池
        logger.debug("bybit_top_up_perp_noop", amount=str(amount))

    async def top_up_spot_margin(self, amount: "Decimal") -> None:
        logger.debug("bybit_top_up_spot_noop", amount=str(amount))

    async def fetch_spot_margin_usdt_balance(self) -> "Decimal":
        from decimal import Decimal as _D  # noqa: PLC0415
        try:
            spot_client = self._clients[InstrumentType.SPOT]
            raw = await spot_client.fetch_balance()
            total = (raw.get("total") or {}).get("USDT") or 0
            return _D(str(total))
        except Exception as exc:
            logger.debug("bybit_fetch_spot_balance_failed", error=str(exc)[:200])
            return _D("0")

    async def fetch_funding_rate(self, symbol: Symbol) -> FundingRate:
        client = self._clients[InstrumentType.PERPETUAL]
        ccxt_symbol = f"{symbol.base}/{symbol.quote}:{symbol.quote}"
        raw = await self._call_with_retry(client.fetch_funding_rate, ccxt_symbol)
        return FundingRate(
            symbol=symbol,
            exchange="bybit",
            rate=_to_dec(raw.get("fundingRate")),
            next_funding_time=int(raw.get("fundingTimestamp") or 0),
            funding_interval_hours=infer_funding_interval_hours(
                raw, default=_DEFAULT_FUNDING_INTERVAL_HOURS,
            ),
        )

    async def fetch_funding_rate_history(
        self, symbol: Symbol, limit: int = 9,
    ) -> List[FundingRate]:
        client = self._clients[InstrumentType.PERPETUAL]
        ccxt_symbol = f"{symbol.base}/{symbol.quote}:{symbol.quote}"
        raw_list = await self._call_with_retry(
            client.fetch_funding_rate_history, ccxt_symbol, None, limit,
        )
        return [
            FundingRate(
                symbol=symbol,
                exchange="bybit",
                rate=_to_dec(r.get("fundingRate")),
                next_funding_time=int(r.get("timestamp") or r.get("fundingTimestamp") or 0),
                funding_interval_hours=infer_funding_interval_hours(
                    r, default=_DEFAULT_FUNDING_INTERVAL_HOURS,
                ),
            )
            for r in (raw_list or [])
        ]

    async def list_usdt_perpetual_symbols(self) -> list[Symbol]:
        client = self._clients[InstrumentType.PERPETUAL]
        markets = await self._call_with_retry(client.load_markets, True)
        result: list[Symbol] = []
        for m in (markets or {}).values():
            if not m.get("active", False):
                continue
            if not m.get("linear", False):  # Bybit linear (USDT) only
                continue
            if not m.get("swap", False):
                continue
            if m.get("quote") != "USDT":
                continue
            base = m.get("base")
            if not base:
                continue
            result.append(Symbol(base, "USDT"))
        return result

    async def fetch_positions(self) -> list:
        return []

    async def fetch_open_orders(self, symbol: Optional[Symbol] = None) -> list:
        return []

    async def fetch_order(self, order_id: str, symbol: Symbol):  # type: ignore[override]
        raise NotImplementedError("BybitAdapter is read-only in market-data mode")

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        raise NotImplementedError("BybitAdapter is read-only in market-data mode")

    async def cancel_all_orders(self, symbol: Optional[Symbol] = None) -> int:
        raise NotImplementedError("BybitAdapter is read-only in market-data mode")

    async def close(self) -> None:
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                pass


def _to_dec(v: object) -> Decimal:
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")
