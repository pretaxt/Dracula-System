"""链上余额查询服务 — Arbitrum One（web3.py 7.x）

查询配置在 /app/state/web3_credentials.json 的钱包地址：
  - ETH（原生代币）
  - USDT（Arbitrum: 0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9）
  - USDC（Arbitrum: 0xaf88d065e77c8cC2239327C5EDb3A432268e5831）

通过 get_onchain_balances() 返回 OnchainBalances；
ETH 价格由 Binance 公开 API 实时拉取（带 5s 超时 + 兜底缓存）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal

import httpx
from eth_account import Account
from web3 import AsyncWeb3, AsyncHTTPProvider

from app.core.logging import get_logger
from app.services.web3_credentials import load_web3_credentials

logger = get_logger(__name__)

# ── Arbitrum One token contracts ─────────────────────────────────────────────
_USDT_ADDR = "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9"
_USDC_ADDR = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

# Minimal ERC-20 ABI：只需 balanceOf + decimals
_ERC20_ABI = [
    {
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
        "stateMutability": "view",
    },
]

# ETH 价格简单缓存（避免频繁请求）
_eth_price_cache: tuple[float, float] | None = None  # (price_usd, timestamp)
_ETH_PRICE_TTL = 60.0  # seconds


@dataclass
class OnchainBalances:
    wallet: str          # checksummed address
    eth: Decimal         # ETH native
    eth_usd: Decimal     # ETH 折算 USD（可能为 0 若无法获取价格）
    usdt: Decimal        # USDT（6 decimals）
    usdc: Decimal        # USDC（6 decimals）
    total_usd: Decimal   # eth_usd + usdt + usdc
    configured: bool     # False → credentials 未配置


async def _get_eth_price_usd() -> float:
    """从 Binance 公开 API 拉 ETH 价格，带缓存和超时兜底。"""
    global _eth_price_cache
    import time  # noqa: PLC0415

    if _eth_price_cache:
        price, ts = _eth_price_cache
        if time.time() - ts < _ETH_PRICE_TTL:
            return price

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "ETHUSDT"},
            )
            data = resp.json()
            price = float(data["price"])
            _eth_price_cache = (price, time.time())
            return price
    except Exception as e:
        logger.warning("eth_price_fetch_failed", error=str(e)[:80])
        return _eth_price_cache[0] if _eth_price_cache else 0.0


async def get_onchain_balances(timeout: float = 8.0) -> OnchainBalances:
    """查询链上余额，整体超时 timeout 秒。失败时返回全零的 OnchainBalances。"""
    creds = load_web3_credentials()
    if not creds.get("private_key") or not creds.get("rpc_url"):
        return OnchainBalances(
            wallet="", eth=Decimal(0), eth_usd=Decimal(0),
            usdt=Decimal(0), usdc=Decimal(0), total_usd=Decimal(0),
            configured=False,
        )

    private_key: str = creds["private_key"]
    rpc_url: str = creds["rpc_url"]

    try:
        wallet = Account.from_key(private_key).address
    except Exception as e:
        logger.warning("onchain_wallet_derive_failed", error=str(e)[:80])
        return OnchainBalances(
            wallet="", eth=Decimal(0), eth_usd=Decimal(0),
            usdt=Decimal(0), usdc=Decimal(0), total_usd=Decimal(0),
            configured=True,
        )

    try:
        result = await asyncio.wait_for(
            _fetch_balances(wallet, rpc_url),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning("onchain_balance_timeout", wallet=wallet[:10])
        return OnchainBalances(
            wallet=wallet, eth=Decimal(0), eth_usd=Decimal(0),
            usdt=Decimal(0), usdc=Decimal(0), total_usd=Decimal(0),
            configured=True,
        )
    except Exception as e:
        logger.warning("onchain_balance_failed", error=str(e)[:120])
        return OnchainBalances(
            wallet=wallet, eth=Decimal(0), eth_usd=Decimal(0),
            usdt=Decimal(0), usdc=Decimal(0), total_usd=Decimal(0),
            configured=True,
        )


async def _fetch_balances(wallet: str, rpc_url: str) -> OnchainBalances:
    """实际 RPC 调用（在 wait_for 超时保护下运行）。"""
    w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
    checksum_wallet = AsyncWeb3.to_checksum_address(wallet)

    usdt_contract = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(_USDT_ADDR), abi=_ERC20_ABI
    )
    usdc_contract = w3.eth.contract(
        address=AsyncWeb3.to_checksum_address(_USDC_ADDR), abi=_ERC20_ABI
    )

    # 并发拉 ETH + USDT + USDC + ETH 价格
    eth_wei, usdt_raw, usdc_raw, eth_price = await asyncio.gather(
        w3.eth.get_balance(checksum_wallet),
        usdt_contract.functions.balanceOf(checksum_wallet).call(),
        usdc_contract.functions.balanceOf(checksum_wallet).call(),
        _get_eth_price_usd(),
    )

    eth = Decimal(eth_wei) / Decimal(10 ** 18)
    usdt = Decimal(usdt_raw) / Decimal(10 ** 6)
    usdc = Decimal(usdc_raw) / Decimal(10 ** 6)
    eth_usd = eth * Decimal(str(eth_price))
    total_usd = eth_usd + usdt + usdc

    logger.debug(
        "onchain_balance_fetched",
        wallet=wallet[:10],
        eth=float(eth),
        usdt=float(usdt),
        usdc=float(usdc),
        total_usd=float(total_usd),
    )

    return OnchainBalances(
        wallet=wallet,
        eth=eth,
        eth_usd=eth_usd,
        usdt=usdt,
        usdc=usdc,
        total_usd=total_usd,
        configured=True,
    )
