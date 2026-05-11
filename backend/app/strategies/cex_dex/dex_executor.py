"""DEX 执行层 — Uniswap V3 Arbitrum exactInputSingle。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal

from app.core.logging import get_logger

from .config import SWAP_ROUTER_ADDRESS, TOKENS

logger = get_logger(__name__)

SWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn",           "type": "address"},
                    {"internalType": "address", "name": "tokenOut",          "type": "address"},
                    {"internalType": "uint24",  "name": "fee",               "type": "uint24"},
                    {"internalType": "address", "name": "recipient",         "type": "address"},
                    {"internalType": "uint256", "name": "amountIn",          "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum",  "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct IV3SwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]

ERC20_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "spender", "type": "address"},
            {"internalType": "uint256", "name": "amount",  "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass
class DexSwapResult:
    success: bool
    token_in: str
    token_out: str
    amount_in_usd: Decimal
    amount_out_usd: Decimal
    gas_used_usd: Decimal
    tx_hash: str = ""
    error: str = ""


class DexExecutor:
    def __init__(self, w3: object, private_key: str, max_slippage_bps: int = 30) -> None:
        from web3 import AsyncWeb3  # noqa: PLC0415
        self._w3: AsyncWeb3 = w3  # type: ignore[assignment]
        self._key = private_key
        self._account = AsyncWeb3.to_checksum_address(
            self._w3.eth.account.from_key(private_key).address
        )
        self._slippage_bps = max_slippage_bps
        self._router = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(SWAP_ROUTER_ADDRESS),
            abi=SWAP_ROUTER_ABI,
        )

    async def swap(
        self,
        token_in_key: str,   # e.g. "USDT"
        token_out_key: str,  # e.g. "WETH"
        amount_in_usd: Decimal,
        expected_out_usd: Decimal,
        pool_fee: int,
        eth_usd_price: Decimal,
    ) -> DexSwapResult:
        from web3 import AsyncWeb3  # noqa: PLC0415
        to_addr = AsyncWeb3.to_checksum_address

        tok_in  = TOKENS[token_in_key]
        tok_out = TOKENS[token_out_key]
        in_dec  = tok_in["decimals"]
        out_dec = tok_out["decimals"]

        amount_in_raw = int(amount_in_usd * 10**in_dec)

        # amountOutMinimum: 按 (1 - slippage_bps/10000) 设置保护
        slippage_factor = Decimal("1") - Decimal(str(self._slippage_bps)) / Decimal("10000")
        min_out_usd = expected_out_usd * slippage_factor

        if token_out_key in ("USDT", "USDC"):
            min_out_raw = int(min_out_usd * 10**out_dec)
        else:
            # out is base token (WETH/WBTC)，需要 usd → token 转换
            base_price = amount_in_usd / (expected_out_usd / amount_in_usd * amount_in_usd)
            min_out_raw = int(min_out_usd / base_price * 10**out_dec) if base_price > 0 else 0

        try:
            # 先 approve token_in → router
            await self._ensure_approval(tok_in["address"], amount_in_raw)

            nonce = await self._w3.eth.get_transaction_count(self._account)
            gas_price = await self._w3.eth.gas_price

            tx = await self._router.functions.exactInputSingle({
                "tokenIn":          to_addr(tok_in["address"]),
                "tokenOut":         to_addr(tok_out["address"]),
                "fee":              pool_fee,
                "recipient":        self._account,
                "amountIn":         amount_in_raw,
                "amountOutMinimum": min_out_raw,
                "sqrtPriceLimitX96": 0,
            }).build_transaction({
                "from":     self._account,
                "nonce":    nonce,
                "gasPrice": gas_price,
                "gas":      300_000,
            })

            signed = self._w3.eth.account.sign_transaction(tx, self._key)
            tx_hash = await self._w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = await self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            gas_used_eth = Decimal(str(receipt["gasUsed"] * gas_price)) / Decimal("1e18")
            gas_used_usd = gas_used_eth * eth_usd_price

            if receipt["status"] == 1:
                logger.info("dex_swap_ok", tx=tx_hash.hex(), gas_usd=float(gas_used_usd))
                return DexSwapResult(
                    success=True, token_in=token_in_key, token_out=token_out_key,
                    amount_in_usd=amount_in_usd, amount_out_usd=expected_out_usd,
                    gas_used_usd=gas_used_usd, tx_hash=tx_hash.hex(),
                )
            else:
                logger.error("dex_swap_reverted", tx=tx_hash.hex())
                return DexSwapResult(
                    success=False, token_in=token_in_key, token_out=token_out_key,
                    amount_in_usd=amount_in_usd, amount_out_usd=Decimal("0"),
                    gas_used_usd=gas_used_usd, tx_hash=tx_hash.hex(), error="reverted",
                )

        except Exception as e:
            logger.exception("dex_swap_failed", pair=f"{token_in_key}→{token_out_key}")
            return DexSwapResult(
                success=False, token_in=token_in_key, token_out=token_out_key,
                amount_in_usd=amount_in_usd, amount_out_usd=Decimal("0"),
                gas_used_usd=Decimal("0"), error=str(e),
            )

    async def _ensure_approval(self, token_address: str, amount: int) -> None:
        from web3 import AsyncWeb3  # noqa: PLC0415
        token = self._w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(token_address),
            abi=ERC20_ABI,
        )
        router_addr = AsyncWeb3.to_checksum_address(SWAP_ROUTER_ADDRESS)
        nonce = await self._w3.eth.get_transaction_count(self._account)
        gas_price = await self._w3.eth.gas_price
        tx = await token.functions.approve(router_addr, amount).build_transaction({
            "from": self._account, "nonce": nonce, "gasPrice": gas_price, "gas": 60_000,
        })
        signed = self._w3.eth.account.sign_transaction(tx, self._key)
        tx_hash = await self._w3.eth.send_raw_transaction(signed.raw_transaction)
        await self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
