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
    # 市值 / 流动性 top 30（USDT 永续覆盖率高）
    "BTC", "ETH", "SOL", "BNB", "XRP",
    "DOGE", "TRX", "ADA", "AVAX", "LINK",
    "DOT", "NEAR", "BCH", "LTC", "UNI",
    "APT", "ARB", "OP", "SUI", "ATOM",
    "FIL", "ETC", "AAVE", "INJ", "ICP",
    "XLM", "RUNE", "SEI", "LDO", "TIA",
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
    """多交易所 spot-perp 基差扫描器。

    Parameters
    ----------
    adapters:
        ``{exchange_name: ExchangeAdapter}`` 已初始化的适配器字典。
    config:
        ``SpotPerpConfig`` 配置（默认 30 标的、0.10% 阈值）。
    exchanges:
        要扫描的交易所列表。``None`` = 所有 adapters 中的交易所；
        否则只扫该列表内交集。
    exchange:
        历史兼容参数（单交易所），等价于 ``exchanges=[exchange]``。
    """

    def __init__(
        self,
        adapters: dict[str, Any],
        config: SpotPerpConfig | None = None,
        exchanges: list[str] | None = None,
        exchange: str | None = None,  # 旧参数；保留兼容
    ) -> None:
        self._adapters = adapters or {}
        self._config = config or SpotPerpConfig()
        if exchanges is not None:
            self._exchanges = [e for e in exchanges if e in self._adapters]
        elif exchange is not None:
            self._exchanges = [exchange] if exchange in self._adapters else []
        else:
            self._exchanges = list(self._adapters.keys())

    async def scan_once(self) -> list[SpotPerpOpportunity]:
        """对所有目标交易所并发扫描，返回按 |basis_pct| 降序的机会列表。"""
        if not self._exchanges:
            logger.warning("spot_perp_no_exchanges")
            return []

        results = await asyncio.gather(
            *(self._scan_one_exchange(ex) for ex in self._exchanges),
            return_exceptions=True,
        )
        flat: list[SpotPerpOpportunity] = []
        for ex, r in zip(self._exchanges, results):
            if isinstance(r, Exception):
                logger.warning("spot_perp_scan_exchange_failed",
                               exchange=ex, error=str(r)[:200])
                continue
            flat.extend(r)
        flat.sort(key=lambda o: abs(o.basis_pct), reverse=True)
        return flat

    async def _scan_one_exchange(
        self, exchange: str,
    ) -> list[SpotPerpOpportunity]:
        adapter = self._adapters.get(exchange)
        if adapter is None:
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

        # B+ enrich missing bid/ask via fetch_bids_asks (Binance USDM ticker 不返 bid/ask)
        await asyncio.gather(
            self._enrich_bid_ask(spot_client, spot_raw, spot_symbols),
            self._enrich_bid_ask(perp_client, perp_raw, perp_symbols),
        )

        opportunities: list[SpotPerpOpportunity] = []
        now_ms = int(time.time() * 1000)
        for base in self._config.symbols:
            spot_sym = f"{base}/USDT"
            perp_sym = f"{base}/USDT:USDT"
            spot_t = spot_raw.get(spot_sym, {}) or {}
            perp_t = perp_raw.get(perp_sym, {}) or {}
            # D.2.x: 用 bid/ask 算"可执行基差"——即市价单实际成交价的近似。
            # 旧版用 ticker.last 已成交过期价导致 scanner 看到的 vs 真实 fill 偏离 0.20-0.30%。
            spot_bid = _to_dec(spot_t.get("bid"))
            spot_ask = _to_dec(spot_t.get("ask"))
            perp_bid = _to_dec(perp_t.get("bid"))
            perp_ask = _to_dec(perp_t.get("ask"))
            spot_last = _to_dec(spot_t.get("last"))
            perp_last = _to_dec(perp_t.get("last"))

            # bid/ask 缺失时回退 last（少数小币种）
            if spot_bid <= 0:
                spot_bid = spot_last
            if spot_ask <= 0:
                spot_ask = spot_last
            if perp_bid <= 0:
                perp_bid = perp_last
            if perp_ask <= 0:
                perp_ask = perp_last
            if spot_bid <= 0 or spot_ask <= 0 or perp_bid <= 0 or perp_ask <= 0:
                continue

            # 同时计算两方向 executable basis，取更有利者上报：
            # - premium: SHORT perp @ perp_bid + LONG spot @ spot_ask → 基差 = perp_bid - spot_ask
            # - discount: LONG perp @ perp_ask + SHORT spot @ spot_bid → 基差 = perp_ask - spot_bid
            premium_basis = perp_bid - spot_ask
            discount_basis = perp_ask - spot_bid
            # 选择最强的方向（绝对值最大且符号正确）
            if premium_basis > 0 and abs(premium_basis) >= abs(discount_basis):
                basis_abs = premium_basis
                direction = "premium"
                spot_px, perp_px = spot_ask, perp_bid  # 用作 reference_price
            elif discount_basis < 0:
                basis_abs = discount_basis
                direction = "discount"
                spot_px, perp_px = spot_bid, perp_ask
            else:
                # 没有可执行机会（spread 跨过基差，bid/ask 把信号吃完）
                continue

            basis_pct = basis_abs / spot_px * Decimal("100")
            if abs(basis_pct) < self._config.min_basis_pct:
                continue

            opportunities.append(
                SpotPerpOpportunity(
                    symbol=spot_sym,
                    exchange=exchange,
                    spot_price=spot_px,
                    perp_price=perp_px,
                    basis_abs=basis_abs,
                    basis_pct=basis_pct,
                    direction=direction,
                    timestamp_ms=now_ms,
                )
            )
        return opportunities

    async def _enrich_bid_ask(
        self, client: Any, raw_tickers: dict[str, Any], symbols: list[str],
    ) -> None:
        """B+ 修复：Binance USDM ticker 不返 bid/ask，用 fetch_bids_asks 批量补全。

        原地修改 raw_tickers，仅填补 bid/ask 缺失项。失败时静默不阻塞。
        """
        if not raw_tickers or not hasattr(client, "fetch_bids_asks"):
            return
        # 提前 short-circuit: CCXT 显式标记不支持时跳过（避免 OKX 每 tick 抛 traceback）
        try:
            if not (getattr(client, "has", {}) or {}).get("fetchBidsAsks", False):
                return
        except Exception:
            pass
        # 找出所有 bid 或 ask 缺失的 symbol
        needs = [
            s for s in symbols
            if (raw_tickers.get(s) or {}).get("bid") is None
            or (raw_tickers.get(s) or {}).get("ask") is None
        ]
        if not needs:
            return
        try:
            book = await asyncio.wait_for(
                client.fetch_bids_asks(needs), timeout=8.0,
            )
        except Exception as exc:
            # NotSupported 是预期的（已在上面 short-circuit，但兜底）；其他异常保留 traceback
            from ccxt.base.errors import NotSupported  # noqa: PLC0415
            if isinstance(exc, NotSupported):
                logger.debug("scanner_fetch_bids_asks_unsupported", error=str(exc)[:80])
            else:
                logger.debug("scanner_fetch_bids_asks_failed", exc_info=True)
            return
        for sym, t in raw_tickers.items():
            if not isinstance(t, dict):
                continue
            b = book.get(sym) if isinstance(book, dict) else None
            if not b:
                continue
            if t.get("bid") is None and b.get("bid") is not None:
                t["bid"] = b["bid"]
            if t.get("ask") is None and b.get("ask") is not None:
                t["ask"] = b["ask"]

    async def _safe_fetch(
        self, adapter: Any, client: Any, symbols: list[str]
    ) -> dict[str, Any]:
        """批量优先；批量失败/超时降级为 per-symbol 直调。

        **绕过 _call_with_retry / rate_limiter**：避免与 funding_rate scanner
        共争同一交易所的 token bucket 导致饥饿。ticker 是公开行情接口，对单条
        请求不走限流可接受；超时 15s（批量）/ 5s（单条）兜底。
        """
        # 批量直调（不走 retry / rate_limiter）
        try:
            raw = await asyncio.wait_for(
                client.fetch_tickers(symbols), timeout=15.0,
            )
            if raw:
                return raw
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "spot_perp_fetch_batch_failed_falling_back_per_symbol",
                error=str(e)[:200],
            )

        # 降级：per-symbol，单标的失败静默
        out: dict[str, Any] = {}
        async def _one(sym: str) -> None:
            try:
                t = await asyncio.wait_for(client.fetch_ticker(sym), timeout=5.0)
                if t:
                    out[sym] = t
            except Exception:
                pass

        await asyncio.gather(*(_one(s) for s in symbols))
        return out
