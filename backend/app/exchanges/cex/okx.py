"""OKXAdapter — OKX 永续合约行情适配器 (只读, 无需 API Key)

用途:
  多交易所行情对比 — 资金费率 / Ticker 差价分析。
  所有调用的均为公开端点, API Key 为空时自动降级为公开模式。

CCXT 映射:
  InstrumentType.PERPETUAL → ccxt.okx(defaultType="swap")
  Symbol 格式: "BTC/USDT:USDT" (CCXT unified linear perp)
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

import ccxt.async_support as ccxt

from app.exchanges.cex.ccxt_base import CCXTAdapter
from app.exchanges.models import FundingRate, InstrumentType, Symbol
from app.core.logging import get_logger

logger = get_logger(__name__)

_OKX_FUNDING_INTERVAL_HOURS = 8


class OKXAdapter(CCXTAdapter):
    """OKX 永续合约适配器。

    无 API Key 时仅支持公开市场数据接口:
      - fetch_tickers
      - fetch_funding_rates
      - fetch_ohlcv
      - fetch_order_book
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        testnet: bool = False,
    ) -> None:
        super().__init__(
            exchange_id="okx",
            api_key=api_key,
            api_secret=api_secret,
            max_rpm=300,
        )

        config: dict = {
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "enableRateLimit": True,
        }

        perp_client = ccxt.okx({
            **config,
            "options": {"defaultType": "swap"},
        })

        spot_client = ccxt.okx({
            **config,
            "options": {"defaultType": "spot"},
        })

        if testnet:
            perp_client.set_sandbox_mode(True)
            spot_client.set_sandbox_mode(True)
            logger.info("okx_testnet_mode", exchange="okx")

        self._clients = {
            InstrumentType.PERPETUAL: perp_client,
            InstrumentType.SPOT: spot_client,
        }

    async def top_up_perp_margin(self, amount: "Decimal") -> None:
        """OKX 统一交易账户：spot 和 swap 共享余额池，无需 inter-wallet 划转。

        策略只需保证 trading account 整体 USDT >= 单笔现货 + 永续保证金。
        """
        logger.debug("okx_top_up_noop", reason="unified_trading_account", amount=str(amount))

    async def top_up_spot_margin(self, amount: "Decimal") -> None:
        """OKX 统一账户：spot margin 共享同一余额池，无需划转。"""
        logger.debug(
            "okx_spot_margin_topup_noop",
            reason="unified_trading_account", amount=str(amount),
        )

    async def fetch_spot_margin_usdt_balance(self) -> "Decimal":
        """OKX 统一账户：直接读 spot client 的 USDT 总余额（与 spot 共享）。"""
        from decimal import Decimal as _D  # noqa: PLC0415
        try:
            spot_client = self._clients[InstrumentType.SPOT]
            raw = await spot_client.fetch_balance()
            total = (raw.get("total") or {}).get("USDT") or 0
            return _D(str(total))
        except Exception as exc:
            logger.debug("okx_fetch_spot_margin_balance_failed", error=str(exc)[:200])
            return _D("0")

    async def fetch_funding_rate(self, symbol: Symbol) -> FundingRate:
        client = self._clients[InstrumentType.PERPETUAL]
        ccxt_symbol = f"{symbol.base}/{symbol.quote}:{symbol.quote}"
        raw = await self._call_with_retry(client.fetch_funding_rate, ccxt_symbol)
        return FundingRate(
            symbol=symbol,
            exchange="okx",
            rate=_to_dec(raw.get("fundingRate")),
            next_funding_time=int(raw.get("fundingTimestamp") or 0),
            funding_interval_hours=_OKX_FUNDING_INTERVAL_HOURS,
        )

    async def fetch_funding_rate_history(
        self, symbol: Symbol, limit: int = 9
    ) -> List[FundingRate]:
        client = self._clients[InstrumentType.PERPETUAL]
        ccxt_symbol = f"{symbol.base}/{symbol.quote}:{symbol.quote}"
        raw_list = await self._call_with_retry(
            client.fetch_funding_rate_history, ccxt_symbol, None, limit
        )
        return [
            FundingRate(
                symbol=symbol,
                exchange="okx",
                rate=_to_dec(r.get("fundingRate")),
                next_funding_time=int(r.get("timestamp") or r.get("fundingTimestamp") or 0),
                funding_interval_hours=_OKX_FUNDING_INTERVAL_HOURS,
            )
            for r in (raw_list or [])
        ]

    async def list_usdt_perpetual_symbols(self) -> list[Symbol]:
        """列出所有 USDT 永续合约 symbol（用于动态扫描所有币对）。"""
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
        raise NotImplementedError("OKXAdapter is read-only in market-data mode")

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        raise NotImplementedError("OKXAdapter is read-only in market-data mode")

    async def cancel_all_orders(self, symbol: Optional[Symbol] = None) -> int:
        raise NotImplementedError("OKXAdapter is read-only in market-data mode")

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
