"""SpotPerpBasisScanner — 现货 vs 永续基差扫描器。

策略思路:
  perp_price > spot_price (premium): short perp + long spot, 等基差收敛
  perp_price < spot_price (discount): long perp + short spot

B.1 范围:
  - 仅监控扫描,不执行
  - 每 60s 拉一次 ticker, |basis_pct| > 阈值的入候选
  - opportunity 列表保存在 runner 内存中,/strategies/spot-perp/opportunities 暴露
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.exchanges.models import InstrumentType

logger = get_logger(__name__)

DEFAULT_SYMBOLS: list[str] = [
    "BTC", "ETH", "SOL", "BNB", "XRP",
    "DOGE", "AVAX", "LINK", "ARB", "OP",
    "SUI", "HYPE",
]

_DEFAULT_MIN_BASIS_PCT = Decimal("0.10")  # |basis| >= 0.10% 算机会


@dataclass
class SpotPerpConfig:
    min_basis_pct: Decimal = _DEFAULT_MIN_BASIS_PCT
    symbols: list[str] = field(default_factory=lambda: list(DEFAULT_SYMBOLS))


@dataclass
class SpotPerpOpportunity:
    symbol: str         # "BTC/USDT"
    exchange: str
    spot_price: Decimal
    perp_price: Decimal
    basis_abs: Decimal
    basis_pct: Decimal
    direction: str      # "premium" | "discount"
    timestamp_ms: int

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "spot_price": str(self.spot_price),
            "perp_price": str(self.perp_price),
            "basis_abs": str(self.basis_abs),
            "basis_pct": str(round(self.basis_pct, 6)),
            "direction": self.direction,
            "timestamp_ms": self.timestamp_ms,
        }


def _to_dec(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return Decimal("0")


class SpotPerpBasisScanner:
    """单交易所 spot-perp 基差扫描器(默认 Binance)。"""

    def __init__(
        self,
        adapters: dict[str, Any],
        config: SpotPerpConfig | None = None,
        exchange: str = "binance",
    ) -> None:
        self._adapters = adapters or {}
        self._config = config or SpotPerpConfig()
        self._exchange = exchange

    async def scan_once(self) -> list[SpotPerpOpportunity]:
        """单次扫描,返回 |basis_pct| 降序的机会列表。"""
        adapter = self._adapters.get(self._exchange)
        if adapter is None:
            logger.warning("spot_perp_adapter_missing", exchange=self._exchange)
            return []

        clients = getattr(adapter, "_clients", {}) or {}
        spot_client = clients.get(InstrumentType.SPOT)
        perp_client = clients.get(InstrumentType.PERPETUAL)
        if spot_client is None or perp_client is None:
            return []

        spot_symbols = [f"{base}/USDT" for base in self._config.symbols]
        perp_symbols = [f"{base}/USDT:USDT" for base in self._config.symbols]

        spot_raw, perp_raw = await asyncio.gather(
            self._safe_fetch(adapter, spot_client, spot_symbols),
            self._safe_fetch(adapter, perp_client, perp_symbols),
        )

        opportunities: list[SpotPerpOpportunity] = []
        now_ms = int(time.time() * 1000)
        for base in self._config.symbols:
            spot_sym = f"{base}/USDT"
            perp_sym = f"{base}/USDT:USDT"
            spot_t = spot_raw.get(spot_sym, {}) or {}
            perp_t = perp_raw.get(perp_sym, {}) or {}
            spot_px = _to_dec(spot_t.get("last"))
            perp_px = _to_dec(perp_t.get("last"))
            if spot_px <= 0 or perp_px <= 0:
                continue

            basis_abs = perp_px - spot_px
            basis_pct = basis_abs / spot_px * Decimal("100")

            if abs(basis_pct) < self._config.min_basis_pct:
                continue

            direction = "premium" if basis_abs > 0 else "discount"
            opportunities.append(
                SpotPerpOpportunity(
                    symbol=spot_sym,
                    exchange=self._exchange,
                    spot_price=spot_px,
                    perp_price=perp_px,
                    basis_abs=basis_abs,
                    basis_pct=basis_pct,
                    direction=direction,
                    timestamp_ms=now_ms,
                )
            )

        opportunities.sort(key=lambda o: abs(o.basis_pct), reverse=True)
        return opportunities

    async def _safe_fetch(
        self, adapter: Any, client: Any, symbols: list[str]
    ) -> dict[str, Any]:
        retry = getattr(adapter, "_call_with_retry", None)
        try:
            if retry is not None:
                raw = await retry(client.fetch_tickers, symbols)
            else:
                raw = await client.fetch_tickers(symbols)
            return raw or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("spot_perp_fetch_tickers_failed", error=str(e))
            return {}
