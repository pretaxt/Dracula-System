"""CEX-DEX 策略 API 端点"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.strategies.cex_dex.config import TOKENS

router = APIRouter(prefix="/strategies", tags=["cex-dex"])

_ERC20_BALANCE_ABI = [
    {
        "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


class CexDexConfigPatch(BaseModel):
    execution_mode: str | None = None
    min_net_profit_usd: float | None = None
    max_trade_usd: float | None = None
    max_daily_loss_usd: float | None = None
    max_gas_gwei: float | None = None


@router.get("/cex-dex/status")
async def cex_dex_status(request: Request) -> dict[str, Any]:
    runner = getattr(request.app.state, "cex_dex_runner", None)
    if runner is None:
        return {"running": False, "mode": "unconfigured",
                "open_trades": 0, "daily_loss_usd": 0.0,
                "max_daily_loss_usd": 0.0, "eth_usd": 0.0,
                "last_scan_at": None}
    return {
        "running": runner._running,
        "mode": runner._cfg.execution_mode,
        "open_trades": runner._open_trades,
        "daily_loss_usd": float(runner._daily_loss),
        "max_daily_loss_usd": float(runner._cfg.max_daily_loss_usd),
        "eth_usd": float(runner._eth_usd),
        "last_scan_at": runner._last_scan_at,
    }


@router.get("/cex-dex/config")
async def cex_dex_config(request: Request) -> dict[str, Any]:
    runner = getattr(request.app.state, "cex_dex_runner", None)
    if runner is None:
        return {}
    cfg = runner._cfg
    return {
        "execution_mode": cfg.execution_mode,
        "scan_interval_seconds": cfg.scan_interval_seconds,
        "max_trade_usd": float(cfg.max_trade_usd),
        "max_daily_loss_usd": float(cfg.max_daily_loss_usd),
        "max_gas_gwei": float(cfg.max_gas_gwei),
        "min_net_profit_usd": float(cfg.min_net_profit_usd),
        "max_slippage_bps": cfg.max_slippage_bps,
        "cex_exchange": cfg.cex_exchange,
        "pairs": [
            {"base": p.base, "quote": p.quote, "pool_fee": p.pool_fee,
             "cex_symbol": p.cex_symbol}
            for p in cfg.pairs
        ],
    }


@router.patch("/cex-dex/config")
async def patch_cex_dex_config(body: CexDexConfigPatch, request: Request) -> dict[str, Any]:
    runner = getattr(request.app.state, "cex_dex_runner", None)
    if runner is None:
        raise HTTPException(status_code=503, detail="runner not running")
    cfg = runner._cfg
    if body.execution_mode is not None:
        if body.execution_mode not in ("paper", "live"):
            raise HTTPException(status_code=422, detail="execution_mode must be paper or live")
        cfg.execution_mode = body.execution_mode
    if body.min_net_profit_usd is not None:
        cfg.min_net_profit_usd = Decimal(str(body.min_net_profit_usd))
    if body.max_trade_usd is not None:
        cfg.max_trade_usd = Decimal(str(body.max_trade_usd))
    if body.max_daily_loss_usd is not None:
        cfg.max_daily_loss_usd = Decimal(str(body.max_daily_loss_usd))
    if body.max_gas_gwei is not None:
        cfg.max_gas_gwei = Decimal(str(body.max_gas_gwei))
    return await cex_dex_config(request)


@router.get("/cex-dex/opportunities")
async def cex_dex_opportunities(request: Request) -> dict[str, Any]:
    runner = getattr(request.app.state, "cex_dex_runner", None)
    if runner is None:
        return {"running": False, "mode": "unconfigured",
                "last_scan_at": None, "min_net_profit_usd": 3.0,
                "eth_usd": 0.0, "data": []}
    opps = getattr(runner, "_last_scan_opps", []) or []
    return {
        "running": runner._running,
        "mode": runner._cfg.execution_mode,
        "last_scan_at": runner._last_scan_at,
        "min_net_profit_usd": float(runner._cfg.min_net_profit_usd),
        "eth_usd": float(runner._eth_usd),
        "data": [
            {
                "pair": o.pair,
                "direction": o.direction,
                "cex_price": str(o.cex_price),
                "dex_price": str(o.dex_price),
                "raw_spread_bps": str(o.raw_spread_bps),
                "estimated_gas_usd": str(o.estimated_gas_usd),
                "net_profit_usd": str(o.net_profit_usd),
                "trade_usd": str(o.trade_usd),
            }
            for o in opps
        ],
    }


@router.get("/cex-dex/wallet-balance")
async def cex_dex_wallet_balance(request: Request) -> dict[str, Any]:
    runner = getattr(request.app.state, "cex_dex_runner", None)
    if runner is None or runner._dex_exec is None:
        return {"configured": False, "wallet_address": None, "balances": [], "total_usd": "0.00"}

    wallet = runner._dex_exec._account
    w3 = runner._w3
    eth_price = float(runner._eth_usd)

    balances: list[dict] = []
    total_usd = 0.0

    # 1. Native ETH
    try:
        eth_wei = await w3.eth.get_balance(wallet)
        eth_amount = float(eth_wei) / 1e18
        eth_usd_val = eth_amount * eth_price
        balances.append({
            "token": "ETH",
            "amount": f"{eth_amount:.6f}",
            "usd": f"{eth_usd_val:.2f}",
        })
        total_usd += eth_usd_val
    except Exception:
        pass

    # 2. ERC20 tokens in TOKENS dict
    from web3 import AsyncWeb3  # noqa: PLC0415
    for token_key, info in TOKENS.items():
        try:
            contract = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(info["address"]),
                abi=_ERC20_BALANCE_ABI,
            )
            raw = await contract.functions.balanceOf(wallet).call()
            amount = float(raw) / (10 ** info["decimals"])
            if amount < 1e-6:
                continue
            if token_key in ("USDT", "USDC"):
                usd_val: float | None = amount
            elif token_key == "WETH":
                usd_val = amount * eth_price
            else:
                usd_val = None  # 其他 token 无实时 USD 换算
            balances.append({
                "token": token_key,
                "amount": f"{amount:.6f}",
                "usd": f"{usd_val:.2f}" if usd_val is not None else "—",
            })
            if usd_val is not None:
                total_usd += usd_val
        except Exception:
            pass

    return {
        "configured": True,
        "wallet_address": wallet,
        "balances": balances,
        "total_usd": f"{total_usd:.2f}",
    }
