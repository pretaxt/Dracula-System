"""#02 perp-basis scanner — 跨所 funding rate 差扫描

数据源：``MarketDataHub`` 的 funding_rates 缓存（不重复打 exchange）。
对每个 (symbol, exchange_pair)，若两边都有最新 funding rate：
  diff_apr_pct = abs(short_apr - long_apr)
  其中 short_exchange = 较高 funding 那一侧（被 short → 收 funding）
       long_exchange  = 较低 funding 那一侧（LONG → 付 funding）
按 diff_apr_pct 降序排列。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from app.core.logging import get_logger
from app.core.market_data_hub import MarketDataHub
from app.exchanges.models import FundingRate, Symbol

logger = get_logger(__name__)

_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class PerpBasisOpportunity:
    """单个 (symbol, exchange_pair) funding 差套利机会。

    long_exchange 的 funding rate < short_exchange 的 funding rate。
    收益方向：在 short_exchange SHORT perp（收 funding）+ long_exchange LONG perp（付 funding）。
    净收益 per 8h ≈ (short_rate - long_rate) × notional
    """
    symbol: str                       # "BTC/USDT"
    long_exchange: str                # 低 funding 端
    short_exchange: str               # 高 funding 端
    long_funding_rate: Decimal        # rate per period (小数, e.g. 0.0001 = 0.01%)
    short_funding_rate: Decimal
    long_apr_pct: Decimal             # 年化百分比
    short_apr_pct: Decimal
    long_funding_interval_hours: int  # 不同所周期可能不同（4h/8h）
    short_funding_interval_hours: int
    long_next_funding_ms: int
    short_next_funding_ms: int
    timestamp: datetime               # opportunity computed time
    health_tier: str = "safe"         # #02-3: safe / risky / dirty

    @property
    def diff_apr_pct(self) -> Decimal:
        """short - long，永远 > 0（按定义 short_apr ≥ long_apr）。"""
        return self.short_apr_pct - self.long_apr_pct

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "long_exchange": self.long_exchange,
            "short_exchange": self.short_exchange,
            "long_funding_rate": str(self.long_funding_rate),
            "short_funding_rate": str(self.short_funding_rate),
            "long_apr_pct": str(round(self.long_apr_pct, 4)),
            "short_apr_pct": str(round(self.short_apr_pct, 4)),
            "diff_apr_pct": str(round(self.diff_apr_pct, 4)),
            "long_funding_interval_hours": self.long_funding_interval_hours,
            "short_funding_interval_hours": self.short_funding_interval_hours,
            "long_next_funding_ms": self.long_next_funding_ms,
            "short_next_funding_ms": self.short_next_funding_ms,
            "timestamp_ms": int(self.timestamp.timestamp() * 1000),
            "health_tier": self.health_tier,
        }


@dataclass
class PerpBasisScannerConfig:
    """perp-basis scanner 参数。"""
    candidate_symbols: list[str] = field(default_factory=list)
    exchange_pairs: list[tuple[str, str]] = field(default_factory=list)
    min_diff_apr_pct: Decimal = Decimal("3.0")
    max_opportunities: int = 50
    max_funding_age_seconds: float = 180.0   # MarketDataHub funding 最大允许 staleness
    # #02-5: 单边 APR 绝对值过滤（防 TIA HTX -99% 类脏数据）
    max_abs_apr_pct: Decimal = Decimal("500.0")
    # #02-3: 健康度分级阈值
    health_safe_diff_apr_max: Decimal = Decimal("200.0")
    health_risky_diff_apr_max: Decimal = Decimal("500.0")


class PerpBasisScanner:
    """从 MarketDataHub 读 funding rates，计算跨所 diff。"""

    def __init__(self, hub: MarketDataHub, config: PerpBasisScannerConfig) -> None:
        self._hub = hub
        self._config = config

    def scan(self) -> list[PerpBasisOpportunity]:
        """同步扫描（无 IO）— 全部数据从 hub 缓存取。"""
        if not self._config.candidate_symbols or not self._config.exchange_pairs:
            return []
        now = datetime.now(timezone.utc)
        opps: list[PerpBasisOpportunity] = []
        for sym_base in self._config.candidate_symbols:
            symbol = Symbol(sym_base.upper(), "USDT")
            sym_str = f"{symbol.base}/{symbol.quote}"
            for ex_a, ex_b in self._config.exchange_pairs:
                rate_a = self._hub.get_funding_rate(
                    ex_a, symbol, max_age_seconds=self._config.max_funding_age_seconds,
                )
                rate_b = self._hub.get_funding_rate(
                    ex_b, symbol, max_age_seconds=self._config.max_funding_age_seconds,
                )
                if rate_a is None or rate_b is None:
                    continue
                a_fr: FundingRate = rate_a.rate
                b_fr: FundingRate = rate_b.rate
                a_apr_pct = a_fr.apr * _HUNDRED
                b_apr_pct = b_fr.apr * _HUNDRED
                # #02-5: 单边 APR 绝对值过滤
                if (abs(a_apr_pct) > self._config.max_abs_apr_pct
                        or abs(b_apr_pct) > self._config.max_abs_apr_pct):
                    continue
                # 选 short = 高 funding 端，long = 低 funding 端
                if a_fr.apr >= b_fr.apr:
                    short_ex, short_fr = ex_a, a_fr
                    long_ex, long_fr = ex_b, b_fr
                else:
                    short_ex, short_fr = ex_b, b_fr
                    long_ex, long_fr = ex_a, a_fr
                diff_apr = (short_fr.apr - long_fr.apr) * _HUNDRED
                if diff_apr < self._config.min_diff_apr_pct:
                    continue
                # #02-3: 健康度分级
                if diff_apr <= self._config.health_safe_diff_apr_max:
                    health = "safe"
                elif diff_apr <= self._config.health_risky_diff_apr_max:
                    health = "risky"
                else:
                    health = "dirty"
                opps.append(PerpBasisOpportunity(
                    symbol=sym_str,
                    long_exchange=long_ex,
                    short_exchange=short_ex,
                    long_funding_rate=long_fr.rate,
                    short_funding_rate=short_fr.rate,
                    long_apr_pct=long_fr.apr * _HUNDRED,
                    short_apr_pct=short_fr.apr * _HUNDRED,
                    long_funding_interval_hours=long_fr.funding_interval_hours,
                    short_funding_interval_hours=short_fr.funding_interval_hours,
                    long_next_funding_ms=long_fr.next_funding_time or 0,
                    short_next_funding_ms=short_fr.next_funding_time or 0,
                    timestamp=now,
                    health_tier=health,
                ))
        opps.sort(key=lambda o: o.diff_apr_pct, reverse=True)
        if self._config.max_opportunities and len(opps) > self._config.max_opportunities:
            opps = opps[: self._config.max_opportunities]
        return opps


def all_pairs(exchanges: Iterable[str]) -> list[tuple[str, str]]:
    """组合 N 家两两配对（顺序去重）。"""
    ex_list = sorted(set(e.lower() for e in exchanges if e))
    out: list[tuple[str, str]] = []
    for i, a in enumerate(ex_list):
        for b in ex_list[i + 1:]:
            out.append((a, b))
    return out
