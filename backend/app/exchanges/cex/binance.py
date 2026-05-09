"""BinanceAdapter — Binance 现货 + USDM 永续适配器

Binance 特殊性:
  - 现货 (spot) 和 USDM 永续 (usdm) 是完全独立的 ccxt client
  - 两个 client 都使用相同的 api_key / api_secret
  - 资金费率只在 usdm client 上有意义
  - CCXT 永续合约 symbol 格式: "BTC/USDT:USDT" (linear perp)

设计来源: docs/07_exchange_adapters.md §3.3
"""
from __future__ import annotations

from typing import List, Optional

from decimal import Decimal

import ccxt.async_support as ccxt

from app.exchanges.cex.ccxt_base import CCXTAdapter, _map_side, _to_decimal
from app.exchanges.errors import DataError, NetworkError
from app.exchanges.models import (
    FundingRate,
    InstrumentType,
    Order,
    Position,
    Symbol,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

# Binance USDM 永续合约资金费结算间隔(小时)
_BINANCE_FUNDING_INTERVAL_HOURS = 8  # 默认；动态推断在 infer_funding_interval_hours

from app.exchanges.cex.funding_interval import infer_funding_interval_hours  # noqa: E402


class BinanceAdapter(CCXTAdapter):
    """Binance 现货 + USDM 永续适配器

    初始化后持有两个 ccxt client:
      InstrumentType.SPOT       → ccxt.async_support.binance
      InstrumentType.PERPETUAL  → ccxt.async_support.binanceusdm
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = False,
    ) -> None:
        super().__init__(
            exchange_id="binance",
            api_key=api_key,
            api_secret=api_secret,
            max_rpm=1200,
        )

        common_config = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,   # CCXT 内置限速作为第二层保护
            "options": {"adjustForTimeDifference": True},
        }

        spot_client = ccxt.binance({
            **common_config,
            "options": {**common_config["options"], "defaultType": "spot"},
        })
        usdm_client = ccxt.binanceusdm({
            **common_config,
            "options": {**common_config["options"], "defaultType": "future"},
        })

        if testnet:
            spot_client.set_sandbox_mode(True)
            usdm_client.set_sandbox_mode(True)
            logger.info("binance_testnet_mode", exchange="binance")

        self._clients = {
            InstrumentType.SPOT: spot_client,
            InstrumentType.PERPETUAL: usdm_client,
        }

    # ------------------------------------------------------------------
    # Funding rate (USDM only)
    # ------------------------------------------------------------------

    async def top_up_perp_margin(self, amount: Decimal) -> None:
        """从 spot 钱包划转 USDT 到 USDM 合约钱包（Universal Transfer API）。

        两个钱包是隔离的：spot wallet 和 USDM futures wallet 余额各自独立。
        永续开仓需要 USDM wallet 有足够保证金，靠 CCXT transfer 实时补足。
        """
        if amount <= 0:
            return
        spot_client = self._clients[InstrumentType.SPOT]
        await spot_client.transfer("USDT", float(amount), "spot", "future")
        logger.info(
            "binance_perp_margin_topped_up",
            from_account="spot",
            to_account="future",
            amount=str(amount),
        )

    async def top_up_spot_margin(self, amount: Decimal) -> None:
        """从 spot 钱包划转 USDT 到现货全仓杠杆钱包（D.2.c 贴水方向开仓前）。

        Binance 现货保证金账户与 spot wallet 隔离；做空需要先转入抵押品。
        """
        if amount <= 0:
            return
        spot_client = self._clients[InstrumentType.SPOT]
        await spot_client.transfer("USDT", float(amount), "spot", "margin")
        logger.info(
            "binance_spot_margin_topped_up",
            from_account="spot",
            to_account="margin",
            amount=str(amount),
        )

    async def fetch_spot_margin_usdt_balance(self) -> Decimal:
        """读现货全仓杠杆账户当前可用 USDT（含借入），失败返回 0。"""
        try:
            spot_client = self._clients[InstrumentType.SPOT]
            # CCXT 统一接口：type=margin 拉杠杆账户余额
            raw = await spot_client.fetch_balance({"type": "margin"})
            total = (raw.get("total") or {}).get("USDT") or 0
            return Decimal(str(total))
        except Exception as exc:
            logger.debug("binance_fetch_spot_margin_balance_failed", error=str(exc)[:200])
            return Decimal("0")

    async def fetch_funding_rate(self, symbol: Symbol) -> FundingRate:
        """拉取 USDM 永续当期资金费率

        CCXT binanceusdm.fetch_funding_rate 返回示例:
          {
            "symbol": "BTC/USDT:USDT",
            "fundingRate": 0.0001,
            "fundingTimestamp": 1700064000000,
            "markPrice": 60000.0,
          }
        """
        usdm = self._clients[InstrumentType.PERPETUAL]
        ccxt_symbol = f"{symbol.base}/{symbol.quote}:{symbol.quote}"

        raw = await self._call_with_retry(
            usdm.fetch_funding_rate, ccxt_symbol
        )
        if not raw:
            raise DataError(
                f"Empty funding rate response for {symbol}",
                exchange="binance",
                symbol=str(symbol),
            )

        predicted: Optional[object] = raw.get("fundingRatePredicted")
        return FundingRate(
            symbol=symbol,
            exchange="binance",
            rate=_to_decimal(raw.get("fundingRate")),
            next_funding_time=int(raw.get("fundingTimestamp") or 0),
            funding_interval_hours=infer_funding_interval_hours(
                raw, default=_BINANCE_FUNDING_INTERVAL_HOURS,
            ),
            predicted_rate=_to_decimal(predicted) if predicted is not None else None,
        )

    async def fetch_funding_rate_history(
        self,
        symbol: Symbol,
        limit: int = 9,
    ) -> List[FundingRate]:
        """拉取历史资金费率 (最近 limit 期)

        用于进场前稳定性检查:
          min_positive_periods = 7 / lookback_periods = 9
        """
        usdm = self._clients[InstrumentType.PERPETUAL]
        ccxt_symbol = f"{symbol.base}/{symbol.quote}:{symbol.quote}"

        raw_list = await self._call_with_retry(
            usdm.fetch_funding_rate_history, ccxt_symbol, None, limit
        )

        return [
            FundingRate(
                symbol=symbol,
                exchange="binance",
                rate=_to_decimal(r.get("fundingRate")),
                next_funding_time=int(r.get("timestamp") or r.get("fundingTimestamp") or 0),
                funding_interval_hours=infer_funding_interval_hours(
                    r, default=_BINANCE_FUNDING_INTERVAL_HOURS,
                ),
            )
            for r in (raw_list or [])
        ]

    async def list_usdt_perpetual_symbols(self) -> List[Symbol]:
        """列出所有 USDT 永续合约 symbol（用于动态扫描所有币对）。

        过滤条件：active=True, quote=USDT, type=swap (perpetual)。
        排除 token-margined 与已下架合约。
        """
        usdm = self._clients[InstrumentType.PERPETUAL]
        markets = await self._call_with_retry(usdm.load_markets, True)
        result: List[Symbol] = []
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

    # ------------------------------------------------------------------
    # Positions (USDM)
    # ------------------------------------------------------------------

    async def fetch_positions(self) -> List[Position]:
        """拉取 USDM 永续当前持仓 (只返回 size > 0 的)"""
        usdm = self._clients[InstrumentType.PERPETUAL]
        raw_list = await self._call_with_retry(usdm.fetch_positions)

        result = []
        for raw in (raw_list or []):
            size = _to_decimal(raw.get("contracts"))
            if size == 0:
                continue
            try:
                sym = Symbol.from_ccxt(raw.get("symbol") or "")
            except ValueError:
                logger.warning(
                    "unknown_position_symbol",
                    exchange="binance",
                    symbol=raw.get("symbol"),
                )
                continue

            liq_price = raw.get("liquidationPrice")
            result.append(Position(
                symbol=sym,
                instrument=InstrumentType.PERPETUAL,
                side=_map_side(raw.get("side") or "long"),
                size=size,
                entry_price=_to_decimal(raw.get("entryPrice")),
                mark_price=_to_decimal(raw.get("markPrice")),
                margin=_to_decimal(raw.get("initialMargin")),
                unrealized_pnl=_to_decimal(raw.get("unrealizedPnl")),
                leverage=_to_decimal(raw.get("leverage")),
                exchange="binance",
                liquidation_price=_to_decimal(liq_price) if liq_price else None,
            ))
        return result

    # ------------------------------------------------------------------
    # Orders — route by trying both clients
    # ------------------------------------------------------------------

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        for client in self._clients.values():
            try:
                await self._call_with_retry(
                    client.cancel_order, order_id, symbol.to_ccxt()
                )
                return True
            except Exception:
                continue
        return False

    async def cancel_all_orders(
        self, symbol: Optional[Symbol] = None
    ) -> int:
        count = 0
        for client in self._clients.values():
            try:
                ccxt_sym = symbol.to_ccxt() if symbol else None
                result = await self._call_with_retry(
                    client.cancel_all_orders, ccxt_sym
                )
                count += len(result) if isinstance(result, list) else 1
            except Exception:
                pass
        return count

    async def fetch_open_orders(
        self, symbol: Optional[Symbol] = None
    ) -> List[Order]:
        orders: List[Order] = []
        for instrument, client in self._clients.items():
            try:
                raw_list = await self._call_with_retry(
                    client.fetch_open_orders,
                    symbol.to_ccxt() if symbol else None,
                )
                for raw in (raw_list or []):
                    try:
                        sym = Symbol.from_ccxt(raw.get("symbol") or "")
                    except ValueError:
                        continue
                    orders.append(self._raw_to_order(raw, sym, instrument))
            except Exception as e:
                logger.warning(
                    "fetch_open_orders_error",
                    exchange="binance",
                    instrument=instrument.value,
                    error=str(e),
                )
        return orders

    async def fetch_order(self, order_id: str, symbol: Symbol) -> Order:
        for instrument, client in [
            (InstrumentType.PERPETUAL, self._clients[InstrumentType.PERPETUAL]),
            (InstrumentType.SPOT, self._clients[InstrumentType.SPOT]),
        ]:
            try:
                raw = await self._call_with_retry(
                    client.fetch_order, order_id, symbol.to_ccxt()
                )
                return self._raw_to_order(raw, symbol, instrument)
            except Exception:
                continue
        raise NetworkError(
            f"fetch_order failed for order_id={order_id} symbol={symbol}",
            exchange="binance",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                pass
