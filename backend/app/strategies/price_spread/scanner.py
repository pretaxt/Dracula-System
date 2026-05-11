"""#03 price-spread scanner — 跨所永续价格差扫描

数据源：``MarketDataHub`` 的 tickers 缓存（同步无 IO）。
对每个 (symbol, exchange_pair)：
  spread_pct = (max_price - min_price) / min_price * 100
  long_exchange  = 低价端（做多）
  short_exchange = 高价端（做空）
按 spread_pct 降序排列。

与 #02 的区别：
  #02 套利 funding rate 差（持仓数天/周收资金费）
  #03 套利 price gap（等价格收敛，通常分钟→小时级）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from app.core.logging import get_logger
from app.core.market_data_hub import MarketDataHub, TickerEntry
from app.exchanges.models import InstrumentType, Symbol

logger = get_logger(__name__)

_HUNDRED = Decimal("100")


@dataclass(frozen=True)
class PriceSpreadOpportunity:
    """单个 (symbol, exchange_pair) 跨所价格差套利机会。

    long_exchange 端价格更低（买入），short_exchange 端价格更高（卖出）。
    净收益 ≈ spread_pct - 双边手续费 - 双边滑点（break-even ≈ 0.25%）
    """
    symbol: str                    # "AR/USDT"
    long_exchange: str             # 低价端
    short_exchange: str            # 高价端
    long_price: Decimal
    short_price: Decimal
    spread_pct: Decimal            # (short - long) / long × 100，>0
    volume_24h_usd_long: Decimal   # long 端 24h 成交额（USDT）
    volume_24h_usd_short: Decimal  # short 端 24h 成交额
    timestamp: datetime

    @property
    def volume_24h_usd_min(self) -> Decimal:
        """两侧最小成交额（执行瓶颈端）。"""
        return min(self.volume_24h_usd_long, self.volume_24h_usd_short)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "long_exchange": self.long_exchange,
            "short_exchange": self.short_exchange,
            "long_price": str(self.long_price),
            "short_price": str(self.short_price),
            "spread_pct": str(round(self.spread_pct, 4)),
            "volume_24h_usd_min": str(round(self.volume_24h_usd_min, 0)),
            "volume_24h_usd_long": str(round(self.volume_24h_usd_long, 0)),
            "volume_24h_usd_short": str(round(self.volume_24h_usd_short, 0)),
            "timestamp_ms": int(self.timestamp.timestamp() * 1000),
        }


@dataclass
class PriceSpreadScannerConfig:
    """price-spread scanner 参数。"""
    candidate_symbols: list[str] = field(default_factory=list)
    exchange_pairs: list[tuple[str, str]] = field(default_factory=list)
    # Phase A 监控阈值（低于 break-even 也显示，用于观察数据分布）
    min_spread_pct: Decimal = Decimal("0.20")
    # 超过此值通常是数据脏点（退市/停盘/价格异常）
    max_spread_pct: Decimal = Decimal("5.0")
    # 两侧都需满足的最小 24h 成交额（USDT）
    min_volume_24h_usd: Decimal = Decimal("2000000")
    max_opportunities: int = 50
    # ticker 允许的最大陈旧度（秒）— 价格比 funding rate 变化快，设严
    max_ticker_age_seconds: float = 60.0


class PriceSpreadScanner:
    """从 MarketDataHub 读 perp 价格，计算跨所价差机会（同步无 IO）。"""

    def __init__(self, hub: MarketDataHub, config: PriceSpreadScannerConfig) -> None:
        self._hub = hub
        self._config = config

    def scan(self) -> list[PriceSpreadOpportunity]:
        """扫描并返回价差机会列表，按 spread_pct 降序。"""
        if not self._config.candidate_symbols or not self._config.exchange_pairs:
            return []
        now = datetime.now(timezone.utc)
        opps: list[PriceSpreadOpportunity] = []

        for sym_base in self._config.candidate_symbols:
            symbol = Symbol(sym_base.upper(), "USDT")
            sym_str = str(symbol)  # "BASE/USDT"

            for ex_a, ex_b in self._config.exchange_pairs:
                te_a = self._hub.get_ticker(
                    ex_a, InstrumentType.PERPETUAL, symbol,
                    max_age_seconds=self._config.max_ticker_age_seconds,
                )
                te_b = self._hub.get_ticker(
                    ex_b, InstrumentType.PERPETUAL, symbol,
                    max_age_seconds=self._config.max_ticker_age_seconds,
                )
                if te_a is None or te_b is None:
                    continue

                price_a = _extract_price(te_a)
                price_b = _extract_price(te_b)
                if price_a is None or price_b is None or price_a <= 0 or price_b <= 0:
                    continue

                vol_a = _extract_volume(te_a)
                vol_b = _extract_volume(te_b)
                if vol_a is None or vol_b is None:
                    continue
                if (vol_a < self._config.min_volume_24h_usd
                        or vol_b < self._config.min_volume_24h_usd):
                    continue

                # long = 低价端，short = 高价端
                if price_a >= price_b:
                    short_ex, short_price, vol_short = ex_a, price_a, vol_a
                    long_ex, long_price, vol_long = ex_b, price_b, vol_b
                else:
                    short_ex, short_price, vol_short = ex_b, price_b, vol_b
                    long_ex, long_price, vol_long = ex_a, price_a, vol_a

                spread_pct = (short_price - long_price) / long_price * _HUNDRED

                if spread_pct < self._config.min_spread_pct:
                    continue
                if spread_pct > self._config.max_spread_pct:
                    # 通常是数据脏点，跳过
                    logger.debug(
                        "price_spread_too_wide",
                        symbol=sym_str, ex_a=ex_a, ex_b=ex_b,
                        spread_pct=str(round(spread_pct, 3)),
                    )
                    continue

                opps.append(PriceSpreadOpportunity(
                    symbol=sym_str,
                    long_exchange=long_ex,
                    short_exchange=short_ex,
                    long_price=long_price,
                    short_price=short_price,
                    spread_pct=spread_pct,
                    volume_24h_usd_long=vol_long,
                    volume_24h_usd_short=vol_short,
                    timestamp=now,
                ))

        opps.sort(key=lambda o: o.spread_pct, reverse=True)
        if self._config.max_opportunities and len(opps) > self._config.max_opportunities:
            opps = opps[: self._config.max_opportunities]
        return opps


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _extract_price(te: TickerEntry) -> Decimal | None:
    """从 raw ticker 取 last price。"""
    raw = te.raw
    val = raw.get("last") or raw.get("close")
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except Exception:  # noqa: BLE001
        return None


def _extract_volume(te: TickerEntry) -> Decimal | None:
    """从 raw ticker 取 24h 成交额（USDT）。
    quoteVolume 优先；OKX swap 无 quoteVolume 时用 baseVolume × last 估算。
    """
    raw = te.raw
    qv = raw.get("quoteVolume")
    if qv is not None:
        try:
            return Decimal(str(qv))
        except Exception:  # noqa: BLE001
            pass
    bv = raw.get("baseVolume")
    last = raw.get("last") or raw.get("close")
    if bv is not None and last is not None:
        try:
            return Decimal(str(bv)) * Decimal(str(last))
        except Exception:  # noqa: BLE001
            pass
    return None


def all_pairs(exchanges: Iterable[str]) -> list[tuple[str, str]]:
    """组合 N 家两两配对（顺序去重），与 #02 perp_basis 同名函数一致。"""
    ex_list = sorted(set(e.lower() for e in exchanges if e))
    out: list[tuple[str, str]] = []
    for i, a in enumerate(ex_list):
        for b in ex_list[i + 1:]:
            out.append((a, b))
    return out
