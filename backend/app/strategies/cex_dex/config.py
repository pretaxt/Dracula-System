"""CEX-DEX 套利策略配置"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List

# Arbitrum One 合约地址
QUOTER_V2_ADDRESS = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"
SWAP_ROUTER_ADDRESS = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"

# Arbitrum token 地址与精度
TOKENS: dict[str, dict] = {
    "WETH":  {"address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "decimals": 18},
    "WBTC":  {"address": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", "decimals": 8},
    "USDT":  {"address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "decimals": 6},
    "USDC":  {"address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "decimals": 6},
    "ARB":   {"address": "0x912CE59144191C1204E64559FE8253a0e49E6548", "decimals": 18},
    "LINK":  {"address": "0xf97f4df75117a78c1A5a0DBb814Af92458539FB4", "decimals": 18},
    "UNI":   {"address": "0xFa7F8980b0f1E64A2062791cc3b0871572f1F7f0", "decimals": 18},
}


@dataclass
class PairConfig:
    base: str            # "ETH"
    quote: str           # "USDT"
    cex_symbol: str      # "ETH/USDT"
    pool_fee: int        # Uniswap V3 fee tier: 500 / 3000 / 10000
    dex_base_token: str  # "WETH"  — key in TOKENS
    dex_quote_token: str # "USDT"


@dataclass
class CexDexConfig:
    instance_name: str = "cex_dex_main"
    enabled: bool = True
    scan_interval_seconds: float = 5.0

    # 执行模式
    execution_mode: str = "paper"  # paper | live

    # 单笔风控
    max_trade_usd: Decimal = Decimal("200")
    max_daily_loss_usd: Decimal = Decimal("100")
    max_gas_gwei: Decimal = Decimal("2.0")
    min_net_profit_usd: Decimal = Decimal("3.0")
    max_slippage_bps: int = 30
    max_open_trades: int = 2

    # CEX
    cex_exchange: str = "binance"
    cex_taker_fee_rate: Decimal = Decimal("0.001")

    pairs: List[PairConfig] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, cfg: dict) -> "CexDexConfig":
        exec_cfg = cfg.get("execution", {})
        cex_cfg = cfg.get("cex", {})

        pairs: list[PairConfig] = []
        for p in cfg.get("pairs", []):
            base = p["base"]
            pairs.append(PairConfig(
                base=base,
                quote=p.get("quote", "USDT"),
                cex_symbol=p.get("cex_symbol", f"{base}/USDT"),
                pool_fee=int(p.get("pool_fee", 500)),
                dex_base_token=p.get("dex_base_token", f"W{base}"),
                dex_quote_token=p.get("dex_quote_token", "USDT"),
            ))

        if not pairs:
            pairs = [PairConfig("ETH", "USDT", "ETH/USDT", 500, "WETH", "USDT")]

        return cls(
            instance_name=cfg.get("instance_name", "cex_dex_main"),
            enabled=cfg.get("enabled", True),
            scan_interval_seconds=float(cfg.get("scan_interval_seconds", 5.0)),
            execution_mode=exec_cfg.get("mode", "paper"),
            max_trade_usd=Decimal(str(exec_cfg.get("max_trade_usd", 200))),
            max_daily_loss_usd=Decimal(str(exec_cfg.get("max_daily_loss_usd", 100))),
            max_gas_gwei=Decimal(str(exec_cfg.get("max_gas_gwei", 2.0))),
            min_net_profit_usd=Decimal(str(exec_cfg.get("min_net_profit_usd", 3.0))),
            max_slippage_bps=int(exec_cfg.get("max_slippage_bps", 30)),
            max_open_trades=int(exec_cfg.get("max_open_trades", 2)),
            cex_exchange=cex_cfg.get("exchange", "binance"),
            cex_taker_fee_rate=Decimal(str(cex_cfg.get("taker_fee_rate", 0.001))),
            pairs=pairs,
        )
