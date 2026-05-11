"""CEX 执行层 — 通过现有 ExchangeAdapter 下市价单。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.core.logging import get_logger
from app.exchanges.base import ExchangeAdapter
from app.exchanges.models import InstrumentType, OrderType, Side, Symbol

logger = get_logger(__name__)


@dataclass
class CexOrderResult:
    success: bool
    side: str        # "buy" | "sell"
    symbol: str
    size: Decimal
    avg_price: Decimal
    fee_usd: Decimal
    order_id: str = ""
    error: str = ""


class CexExecutor:
    def __init__(self, adapter: ExchangeAdapter, taker_fee_rate: Decimal = Decimal("0.001")) -> None:
        self._adapter = adapter
        self._fee_rate = taker_fee_rate

    async def market_buy(self, cex_symbol: str, usd_amount: Decimal, ref_price: Decimal) -> CexOrderResult:
        size = usd_amount / ref_price
        return await self._place(cex_symbol, Side.BUY, size, ref_price, usd_amount)

    async def market_sell(self, cex_symbol: str, usd_amount: Decimal, ref_price: Decimal) -> CexOrderResult:
        size = usd_amount / ref_price
        return await self._place(cex_symbol, Side.SELL, size, ref_price, usd_amount)

    async def _place(
        self,
        cex_symbol: str,
        side: Side,
        size: Decimal,
        ref_price: Decimal,
        usd_amount: Decimal,
    ) -> CexOrderResult:
        symbol = Symbol.from_ccxt(cex_symbol)
        client_id = f"cex_dex_{uuid.uuid4().hex[:12]}"
        try:
            order = await self._adapter.place_order(
                symbol=symbol,
                instrument=InstrumentType.SPOT,
                side=side,
                order_type=OrderType.MARKET,
                size=size,
                client_order_id=client_id,
            )
            fill_price = order.avg_fill_price or ref_price
            fee_usd = usd_amount * self._fee_rate
            logger.info(
                "cex_order_placed",
                side=side.value, symbol=cex_symbol,
                size=float(size), fill_price=float(fill_price), order_id=order.order_id,
            )
            return CexOrderResult(
                success=True, side=side.value, symbol=cex_symbol,
                size=size, avg_price=fill_price, fee_usd=fee_usd, order_id=order.order_id,
            )
        except Exception as e:
            logger.exception("cex_order_failed", side=side.value, symbol=cex_symbol)
            return CexOrderResult(
                success=False, side=side.value, symbol=cex_symbol,
                size=size, avg_price=Decimal("0"), fee_usd=Decimal("0"), error=str(e),
            )
