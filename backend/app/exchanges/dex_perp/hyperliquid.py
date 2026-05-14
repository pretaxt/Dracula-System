"""HyperliquidAdapter — Hyperliquid DEX perpetual + spot 适配器。

Phase A (本文件): read-only 公开数据
  - fetch_tickers (230 perp + spot)
  - fetch_funding_rate (1h funding interval)
  - 不需要私钥，纯消费公开 API

Phase B/C (TODO): 接交易需要 EVM 钱包私钥
  - 推荐用 API Wallet 子账号（read+trade only, 不能 withdraw）
  - 私钥走 env / state/exchange_credentials.json，绝不入代码
"""
from __future__ import annotations

from decimal import Decimal

import ccxt.async_support as ccxt

from app.core.logging import get_logger
from app.exchanges.cex.ccxt_base import CCXTAdapter
from app.exchanges.cex.funding_interval import infer_funding_interval_hours
from app.exchanges.models import FundingRate, InstrumentType, Symbol

logger = get_logger(__name__)

# Hyperliquid funding 每小时累积+结算（hourly funding，不是 8h）
_DEFAULT_FUNDING_INTERVAL_HOURS = 1


def _to_dec(v) -> Decimal:
    try:
        return Decimal(str(v)) if v is not None else Decimal("0")
    except Exception:
        return Decimal("0")


class HyperliquidAdapter(CCXTAdapter):
    """Hyperliquid DEX 适配器（Phase A: read-only）。"""

    def __init__(
        self,
        wallet_address: str = "",
        api_wallet_private_key: str = "",
        testnet: bool = False,
    ) -> None:
        super().__init__(
            exchange_id="hyperliquid",
            api_key=wallet_address,
            api_secret=api_wallet_private_key,
            max_rpm=120,
        )
        config: dict = {"enableRateLimit": True}
        if wallet_address:
            config["walletAddress"] = wallet_address
        if api_wallet_private_key:
            config["privateKey"] = api_wallet_private_key

        perp_client = ccxt.hyperliquid({
            **config,
            "options": {"defaultType": "swap"},
        })
        spot_client = ccxt.hyperliquid({
            **config,
            "options": {"defaultType": "spot"},
        })
        if testnet:
            perp_client.set_sandbox_mode(True)
            spot_client.set_sandbox_mode(True)
            logger.info("hyperliquid_testnet_mode")
        self._clients = {
            InstrumentType.PERPETUAL: perp_client,
            InstrumentType.SPOT: spot_client,
        }

    async def fetch_balance(self) -> "Balance":
        """Hyperliquid 余额。

        Hyperliquid 是 USDC 结算 DEX，但 Dashboard sum 用 `usdt_keys` 白名单，
        所以把 USDC 总值包装成 USDT entry，让 dashboard 正确累加（USDC ≈ 1 USD）。
        其他币种保留原 asset name（详情页可见，不重复 sum）。
        """
        import time as _time  # noqa: PLC0415
        from app.exchanges.models import Balance, BalanceEntry  # noqa: PLC0415
        client = self._clients[InstrumentType.PERPETUAL]
        raw = await self._call_with_retry(client.fetch_balance)

        usdc_total = Decimal(str((raw.get("total") or {}).get("USDC") or 0))
        usdc_free = Decimal(str((raw.get("free") or {}).get("USDC") or 0))
        usdc_locked = Decimal(str((raw.get("used") or {}).get("USDC") or 0))

        entries: list[BalanceEntry] = []
        # USDT entry: hyperliquid 用 USDC，但 dashboard sum 只识别 USDT 系列 key
        # 把 USDC 总值塞进 USDT entry 让 total_equity 正确包含 hyperliquid
        if usdc_total > 0:
            entries.append(BalanceEntry(
                asset="USDT",
                free=usdc_free,
                locked=usdc_locked,
            ))

        # 其他币种保留（详情页可见，不影响 total）
        for asset, total_val in (raw.get("total") or {}).items():
            if total_val is None or asset.upper() in ("USDT", "USDC"):
                continue
            free_val = Decimal(str((raw.get("free") or {}).get(asset) or 0))
            locked_val = Decimal(str((raw.get("used") or {}).get(asset) or 0))
            entries.append(BalanceEntry(
                asset=asset.upper(),
                free=free_val,
                locked=locked_val,
            ))
        return Balance(
            entries=entries,
            timestamp=int(_time.time() * 1000),
        )

    async def list_usdt_perpetual_symbols(self) -> list[Symbol]:
        """返回 hyperliquid 全部活跃 perp 标的。

        注：method 名 'usdt' 是历史命名（其他 adapter 都是 USDT 结算），
        hyperliquid 实际是 USDC 结算，返回 Symbol(base, quote='USDC')。
        策略层（perp_basis_scanner）需要决定如何处理 USDT/USDC 跨 quote 套利。
        """
        client = self._clients[InstrumentType.PERPETUAL]
        markets = await self._call_with_retry(client.load_markets, True)
        result: list[Symbol] = []
        for m in (markets or {}).values():
            if not m.get("active", False):
                continue
            if not m.get("swap", False):
                continue
            # hyperliquid: linear USDC perp（settle=USDC, quote=USDC）
            if m.get("quote") != "USDC":
                continue
            base = m.get("base")
            if not base:
                continue
            result.append(Symbol(base=base, quote="USDC"))
        return result

    async def fetch_funding_rate(self, symbol: Symbol) -> FundingRate:
        client = self._clients[InstrumentType.PERPETUAL]
        # hyperliquid 用 BASE/USDC:USDC 格式（USDC 结算）
        ccxt_symbol = f"{symbol.base}/{symbol.quote}:{symbol.quote}"
        raw = await self._call_with_retry(client.fetch_funding_rate, ccxt_symbol)
        return FundingRate(
            symbol=symbol,
            exchange="hyperliquid",
            rate=_to_dec(raw.get("fundingRate")),
            next_funding_time=int(raw.get("fundingTimestamp") or 0),
            interval_hours=infer_funding_interval_hours(
                raw, default=_DEFAULT_FUNDING_INTERVAL_HOURS
            ),
        )
