"""BitgetAdapter — Bitget 现货 + USDT-M 永续适配器。

设计与 OKX 相似（统一账户共享 USDT 余额），funding 周期 4h/8h 动态识别。
为 #04 spot-perp / #02 跨所基差套利 提供数据源。

API key 为空时仅支持公开市场数据接口（无需 trading 权限即可扫描）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

import ccxt.async_support as ccxt

from app.core.logging import get_logger
from app.exchanges.cex.ccxt_base import CCXTAdapter
from app.exchanges.cex.funding_interval import infer_funding_interval_hours
from app.exchanges.models import FundingRate, InstrumentType, Symbol

logger = get_logger(__name__)

_DEFAULT_FUNDING_INTERVAL_HOURS = 8  # Bitget 主流 8h，alts 多为 4h（动态识别）


class BitgetAdapter(CCXTAdapter):
    """Bitget 永续合约 + 现货适配器。"""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        testnet: bool = False,
    ) -> None:
        super().__init__(
            exchange_id="bitget",
            api_key=api_key,
            api_secret=api_secret,
            max_rpm=500,
        )

        config: dict = {
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "enableRateLimit": True,
        }

        # Bitget USDT-M perpetual
        perp_client = ccxt.bitget({
            **config,
            "options": {"defaultType": "swap"},
        })
        spot_client = ccxt.bitget({
            **config,
            "options": {"defaultType": "spot"},
        })

        if testnet:
            perp_client.set_sandbox_mode(True)
            spot_client.set_sandbox_mode(True)
            logger.info("bitget_testnet_mode")

        self._clients = {
            InstrumentType.PERPETUAL: perp_client,
            InstrumentType.SPOT: spot_client,
        }

    # ------------------------------------------------------------------
    # 资金管理 — Bitget 默认 unified-style 账户，spot/futures 余额可单独查
    # ------------------------------------------------------------------

    async def top_up_perp_margin(self, amount: "Decimal") -> None:
        """Bitget USDT-M 与现货账户分离 — 暂用 noop（用户手动转账）。"""
        logger.debug("bitget_top_up_perp_noop", amount=str(amount))

    async def top_up_spot_margin(self, amount: "Decimal") -> None:
        logger.debug("bitget_top_up_spot_noop", amount=str(amount))

    async def fetch_spot_margin_usdt_balance(self) -> "Decimal":
        """读 spot 账户 USDT 余额。"""
        from decimal import Decimal as _D  # noqa: PLC0415
        try:
            spot_client = self._clients[InstrumentType.SPOT]
            raw = await spot_client.fetch_balance()
            total = (raw.get("total") or {}).get("USDT") or 0
            return _D(str(total))
        except Exception as exc:
            logger.debug("bitget_fetch_spot_balance_failed", error=str(exc)[:200])
            return _D("0")

    # ------------------------------------------------------------------
    # 行情
    # ------------------------------------------------------------------

    async def fetch_funding_rate(self, symbol: Symbol) -> FundingRate:
        client = self._clients[InstrumentType.PERPETUAL]
        ccxt_symbol = f"{symbol.base}/{symbol.quote}:{symbol.quote}"
        raw = await self._call_with_retry(client.fetch_funding_rate, ccxt_symbol)
        return FundingRate(
            symbol=symbol,
            exchange="bitget",
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
                exchange="bitget",
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
        raise NotImplementedError("BitgetAdapter is read-only in market-data mode")

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        raise NotImplementedError("BitgetAdapter is read-only in market-data mode")

    async def cancel_all_orders(self, symbol: Optional[Symbol] = None) -> int:
        raise NotImplementedError("BitgetAdapter is read-only in market-data mode")

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
