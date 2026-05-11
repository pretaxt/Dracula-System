"""CEX-DEX 价格扫描器 — 比对 CEX 与 Uniswap V3 Arbitrum 价格，计算净利润。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import List

from app.core.logging import get_logger
from app.exchanges.base import ExchangeAdapter
from app.exchanges.models import InstrumentType, Symbol

from .config import CexDexConfig, PairConfig, TOKENS, QUOTER_V2_ADDRESS

logger = get_logger(__name__)

# 用 $1000 向 QuoterV2 查报价，减少极小金额的精度损失
_QUOTE_NOTIONAL_USD = Decimal("1000")

QUOTER_V2_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address",  "name": "tokenIn",           "type": "address"},
                    {"internalType": "address",  "name": "tokenOut",          "type": "address"},
                    {"internalType": "uint256",  "name": "amountIn",          "type": "uint256"},
                    {"internalType": "uint24",   "name": "fee",               "type": "uint24"},
                    {"internalType": "uint160",  "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IQuoterV2.QuoteExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut",                  "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After",          "type": "uint160"},
            {"internalType": "uint32",  "name": "initializedTicksCrossed",    "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate",                "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


@dataclass
class CexDexOpportunity:
    pair: str               # "ETH/USDT"
    direction: str          # "cex_cheap" | "dex_cheap"
    cex_price: Decimal      # CEX ask (cex_cheap) 或 bid (dex_cheap)
    dex_price: Decimal      # DEX 等效价格（已含 pool fee 滑点）
    raw_spread_bps: Decimal
    estimated_gas_usd: Decimal
    net_profit_usd: Decimal  # 按 max_trade_usd 计算
    trade_usd: Decimal
    pool_fee: int            # 500 = 0.05%


class CexDexScanner:
    def __init__(
        self,
        config: CexDexConfig,
        w3: object,          # AsyncWeb3
        cex_adapter: ExchangeAdapter,
        eth_usd_price: Decimal = Decimal("3000"),
    ) -> None:
        self._cfg = config
        self._w3 = w3
        self._cex = cex_adapter
        self._eth_usd = eth_usd_price

        from web3 import AsyncWeb3  # noqa: PLC0415
        self._quoter = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(QUOTER_V2_ADDRESS),
            abi=QUOTER_V2_ABI,
        )

    async def scan(self) -> List[CexDexOpportunity]:
        tasks = [self._scan_pair(p) for p in self._cfg.pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        opps: list[CexDexOpportunity] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("cex_dex_scan_pair_error", exc_info=r)
            elif r is not None:
                opps.append(r)
        return opps

    async def _scan_pair(self, pair: PairConfig) -> CexDexOpportunity | None:
        symbol = Symbol.from_ccxt(pair.cex_symbol)
        try:
            ticker, dex_prices = await asyncio.gather(
                self._cex.fetch_ticker(symbol, InstrumentType.SPOT),
                self._fetch_dex_prices(pair),
            )
        except Exception:
            logger.warning("cex_dex_pair_fetch_failed", pair=pair.cex_symbol, exc_info=True)
            return None

        if dex_prices is None:
            return None

        dex_buy_price, dex_sell_price = dex_prices
        gas_usd = await self._estimate_gas_usd()
        trade_usd = self._cfg.max_trade_usd
        cex_fee = trade_usd * self._cfg.cex_taker_fee_rate * 2  # 买+卖两腿
        dex_fee = trade_usd * Decimal(str(pair.pool_fee)) / Decimal("1_000_000")

        # cex_cheap: 在 CEX ask 买入，在 DEX 卖出
        if dex_sell_price > ticker.ask:
            spread_bps = (dex_sell_price - ticker.ask) / ticker.ask * Decimal("10000")
            base_qty = trade_usd / ticker.ask
            gross = base_qty * (dex_sell_price - ticker.ask)
            net = gross - cex_fee - dex_fee - gas_usd
            if net >= self._cfg.min_net_profit_usd:
                return CexDexOpportunity(
                    pair=pair.cex_symbol, direction="cex_cheap",
                    cex_price=ticker.ask, dex_price=dex_sell_price,
                    raw_spread_bps=spread_bps, estimated_gas_usd=gas_usd,
                    net_profit_usd=net, trade_usd=trade_usd, pool_fee=pair.pool_fee,
                )

        # dex_cheap: 在 DEX 买入，在 CEX bid 卖出
        if ticker.bid > dex_buy_price:
            spread_bps = (ticker.bid - dex_buy_price) / dex_buy_price * Decimal("10000")
            base_qty = trade_usd / dex_buy_price
            gross = base_qty * (ticker.bid - dex_buy_price)
            net = gross - cex_fee - dex_fee - gas_usd
            if net >= self._cfg.min_net_profit_usd:
                return CexDexOpportunity(
                    pair=pair.cex_symbol, direction="dex_cheap",
                    cex_price=ticker.bid, dex_price=dex_buy_price,
                    raw_spread_bps=spread_bps, estimated_gas_usd=gas_usd,
                    net_profit_usd=net, trade_usd=trade_usd, pool_fee=pair.pool_fee,
                )

        return None

    async def _fetch_dex_prices(self, pair: PairConfig) -> tuple[Decimal, Decimal] | None:
        """返回 (dex_buy_price, dex_sell_price)，单位：quote per base。"""
        base_tok = TOKENS.get(pair.dex_base_token)
        quote_tok = TOKENS.get(pair.dex_quote_token)
        if not base_tok or not quote_tok:
            logger.warning("unknown_dex_token", base=pair.dex_base_token, quote=pair.dex_quote_token)
            return None

        bd = base_tok["decimals"]
        qd = quote_tok["decimals"]
        fee = pair.pool_fee
        notional_q = int(_QUOTE_NOTIONAL_USD * 10**qd)

        from web3 import AsyncWeb3  # noqa: PLC0415
        to_addr = AsyncWeb3.to_checksum_address

        try:
            # quote→base: 用 USDT 买 ETH，得到 DEX 的 buy price
            out_b = await self._quoter.functions.quoteExactInputSingle({
                "tokenIn":  to_addr(quote_tok["address"]),
                "tokenOut": to_addr(base_tok["address"]),
                "amountIn": notional_q,
                "fee": fee,
                "sqrtPriceLimitX96": 0,
            }).call()
            base_received = Decimal(str(out_b[0])) / Decimal(str(10**bd))
            dex_buy_price = _QUOTE_NOTIONAL_USD / base_received

            # base→quote: 用 ETH 买 USDT，得到 DEX 的 sell price
            notional_b = int(base_received * 10**bd)
            out_q = await self._quoter.functions.quoteExactInputSingle({
                "tokenIn":  to_addr(base_tok["address"]),
                "tokenOut": to_addr(quote_tok["address"]),
                "amountIn": notional_b,
                "fee": fee,
                "sqrtPriceLimitX96": 0,
            }).call()
            quote_received = Decimal(str(out_q[0])) / Decimal(str(10**qd))
            dex_sell_price = quote_received / base_received

            return dex_buy_price, dex_sell_price

        except Exception:
            logger.warning("dex_quote_failed", pair=pair.cex_symbol, exc_info=True)
            return None

    async def _estimate_gas_usd(self) -> Decimal:
        try:
            gas_price_wei = await self._w3.eth.gas_price
            gas_units = 200_000  # Uniswap V3 单次 swap 典型值
            gas_eth = Decimal(str(gas_price_wei * gas_units)) / Decimal("1e18")
            return gas_eth * self._eth_usd
        except Exception:
            return Decimal("0.05")  # Arbitrum 兜底估计
