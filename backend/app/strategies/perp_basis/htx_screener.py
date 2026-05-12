"""HTX 结构性溢价筛选器

从 CCXT 拉 N 天 funding history，对每个 (symbol, HTX vs ref_exchange) 对计算：
  - avg_diff_apr_pct   : 平均 APR 差（正 = HTX 是高 funding 端）
  - persistence_pct    : HTX 为高 funding 端的时间占比
  - stddev_diff_apr    : 标准差（越低越稳定）
  - score              : avg_diff × persistence / 100（综合排名分）
  - break_even_hours   : 4 腿手续费回本所需持仓小时数

用途：定期运行，更新 candidate_symbols 配置。
"""
from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.backtest.perp_basis_loader import load_funding_history
from app.backtest.perp_basis_models import PerpFundingSnapshot
from app.core.logging import get_logger

logger = get_logger(__name__)

# 4 腿（2 open + 2 close）× avg taker 0.055%
_ROUNDTRIP_FEE_PCT = Decimal("0.22")
# 每年 8h 结算次数
_PERIODS_PER_YEAR = Decimal("1095")
# 时间戳对齐容差 (秒)
_ALIGN_TOLERANCE_SECS = 4 * 3600  # ±4h

# 默认参与对比的参考交易所（优先级顺序）
_DEFAULT_REF_EXCHANGES = ("binance", "okx", "bybit")


@dataclass
class HTXSymbolResult:
    """单个 symbol 的 HTX 溢价分析结果。"""
    symbol: str                      # "ENJ/USDT"
    ref_exchange: str                # 对比方
    avg_diff_apr_pct: Decimal        # 平均 APR 差（正 = HTX 高）
    persistence_pct: Decimal         # HTX 高 funding 期间占比 %
    stddev_diff_apr_pct: Decimal     # diff_apr 的标准差
    score: Decimal                   # avg_diff × persistence / 100
    sample_count: int                # 对齐样本数
    max_diff_apr_pct: Decimal        # 最大 diff（峰值）
    last_diff_apr_pct: Decimal       # 最近一个 diff
    htx_avg_apr_pct: Decimal         # HTX 侧平均 APR
    ref_avg_apr_pct: Decimal         # 参考侧平均 APR
    break_even_hours: Decimal        # 按 avg_diff 估算的回本小时数

    @property
    def recommendation(self) -> str:
        """基于 score 和 persistence 给出建议标签。

        STRONG : score≥40 且 persistence≥80%（结构性稳定溢价，可信度最高）
        MODERATE: score≥20 且 persistence≥60%（有一定持续性）
        WEAK   : score≥8  且 persistence≥40%（偶发性，慎入）
        NOISE  : 噪声
        """
        if self.score >= Decimal("40") and self.persistence_pct >= Decimal("80"):
            return "STRONG"
        if self.score >= Decimal("20") and self.persistence_pct >= Decimal("60"):
            return "MODERATE"
        if self.score >= Decimal("8") and self.persistence_pct >= Decimal("40"):
            return "WEAK"
        return "NOISE"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "ref_exchange": self.ref_exchange,
            "avg_diff_apr_pct": str(round(self.avg_diff_apr_pct, 2)),
            "persistence_pct": str(round(self.persistence_pct, 1)),
            "stddev_diff_apr_pct": str(round(self.stddev_diff_apr_pct, 2)),
            "score": str(round(self.score, 2)),
            "sample_count": self.sample_count,
            "max_diff_apr_pct": str(round(self.max_diff_apr_pct, 2)),
            "last_diff_apr_pct": str(round(self.last_diff_apr_pct, 2)),
            "htx_avg_apr_pct": str(round(self.htx_avg_apr_pct, 2)),
            "ref_avg_apr_pct": str(round(self.ref_avg_apr_pct, 2)),
            "break_even_hours": str(round(self.break_even_hours, 1)),
            "recommendation": self.recommendation,
        }


@dataclass
class HTXScreenResult:
    """筛选结果汇总。"""
    generated_at: datetime
    days_analyzed: int
    symbols_screened: int                    # 有足够数据的 symbol 总数
    results: list[HTXSymbolResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "days_analyzed": self.days_analyzed,
            "symbols_screened": self.symbols_screened,
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _annualize(rate: Decimal, interval_hours: int) -> Decimal:
    """把 per-period rate 转为年化 % APR。"""
    if interval_hours <= 0:
        interval_hours = 8
    return rate * (Decimal("8760") / Decimal(str(interval_hours))) * Decimal("100")


def _group_snaps(
    snaps: list[PerpFundingSnapshot],
) -> dict[tuple[str, str], list[PerpFundingSnapshot]]:
    """按 (exchange, symbol) 分组，每组内按时间升序。"""
    groups: dict[tuple[str, str], list[PerpFundingSnapshot]] = {}
    for s in snaps:
        key = (s.exchange, s.symbol)
        groups.setdefault(key, []).append(s)
    for lst in groups.values():
        lst.sort(key=lambda x: x.timestamp)
    return groups


def _align_and_diff(
    htx_snaps: list[PerpFundingSnapshot],
    ref_snaps: list[PerpFundingSnapshot],
) -> list[Decimal]:
    """时间对齐后计算每对 diff_apr = htx_apr - ref_apr（+ = HTX 溢价）。

    对每个 HTX snapshot，在 ref_snaps 中找距离最近（≤ 4h）的记录。
    """
    if not htx_snaps or not ref_snaps:
        return []

    diffs: list[Decimal] = []
    ref_ts = [int(s.timestamp.timestamp()) for s in ref_snaps]

    for h in htx_snaps:
        h_ts = int(h.timestamp.timestamp())
        # 找最近 ref snap
        best_idx, best_delta = 0, abs(ref_ts[0] - h_ts)
        for i, rt in enumerate(ref_ts):
            delta = abs(rt - h_ts)
            if delta < best_delta:
                best_delta = delta
                best_idx = i
        if best_delta > _ALIGN_TOLERANCE_SECS:
            continue  # 超出对齐容差，跳过
        ref_s = ref_snaps[best_idx]
        htx_apr = _annualize(h.funding_rate, h.funding_interval_hours)
        ref_apr = _annualize(ref_s.funding_rate, ref_s.funding_interval_hours)
        diffs.append(htx_apr - ref_apr)

    return diffs


def _compute_metrics(
    symbol: str,
    ref_exchange: str,
    htx_snaps: list[PerpFundingSnapshot],
    ref_snaps: list[PerpFundingSnapshot],
    min_positive_threshold_apr: Decimal = Decimal("5.0"),
) -> HTXSymbolResult | None:
    """给定对齐的 HTX/ref 快照列表，计算分析指标。"""
    diffs = _align_and_diff(htx_snaps, ref_snaps)
    if not diffs:
        return None

    n = len(diffs)
    avg_diff = sum(diffs, Decimal("0")) / Decimal(str(n))

    # 标准差（需要 n≥2）
    if n >= 2:
        float_diffs = [float(d) for d in diffs]
        stddev = Decimal(str(statistics.stdev(float_diffs)))
    else:
        stddev = Decimal("0")

    # persistence: HTX 为高端且 diff > threshold 的占比
    positive_count = sum(1 for d in diffs if d > min_positive_threshold_apr)
    persistence = Decimal(str(positive_count)) / Decimal(str(n)) * Decimal("100")

    max_diff = max(diffs)
    last_diff = diffs[-1] if diffs else Decimal("0")

    # HTX/ref 自身平均 APR
    htx_aprs = [_annualize(s.funding_rate, s.funding_interval_hours) for s in htx_snaps]
    ref_aprs = [_annualize(s.funding_rate, s.funding_interval_hours) for s in ref_snaps]
    htx_avg = sum(htx_aprs, Decimal("0")) / Decimal(str(len(htx_aprs))) if htx_aprs else Decimal("0")
    ref_avg = sum(ref_aprs, Decimal("0")) / Decimal(str(len(ref_aprs))) if ref_aprs else Decimal("0")

    # score
    score = avg_diff * persistence / Decimal("100")

    # break-even hours: roundtrip_fee / (avg_diff / periods_per_year) / 8
    # = roundtrip_fee_pct / 100 / (avg_diff_pct / 100 / 1095) * 8
    # = roundtrip_fee_pct * 1095 * 8 / avg_diff_pct
    if avg_diff > Decimal("0"):
        break_even = _ROUNDTRIP_FEE_PCT * _PERIODS_PER_YEAR * Decimal("8") / avg_diff
    else:
        break_even = Decimal("9999")

    return HTXSymbolResult(
        symbol=symbol,
        ref_exchange=ref_exchange,
        avg_diff_apr_pct=avg_diff,
        persistence_pct=persistence,
        stddev_diff_apr_pct=stddev,
        score=score,
        sample_count=n,
        max_diff_apr_pct=max_diff,
        last_diff_apr_pct=last_diff,
        htx_avg_apr_pct=htx_avg,
        ref_avg_apr_pct=ref_avg,
        break_even_hours=break_even,
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

async def screen_htx_premium(
    adapters: dict[str, Any],
    hub: Any | None = None,
    days: int = 14,
    ref_exchanges: tuple[str, ...] = _DEFAULT_REF_EXCHANGES,
    min_persistence_pct: float = 40.0,
    min_avg_diff_apr: float = 10.0,
    min_samples: int = 8,
    limit: int = 50,
) -> HTXScreenResult:
    """HTX 溢价筛选主函数。

    Parameters
    ----------
    adapters      : app.state.adapters  (exchange_name → ExchangeAdapter)
    hub           : app.state.market_data_hub (用于获取当前 symbol 列表)
    days          : 拉取历史天数（建议 14）
    ref_exchanges : 参考交易所优先级列表
    min_persistence_pct : 最低 HTX 高 funding 持续性要求（%）
    min_avg_diff_apr    : 最低平均 APR 差（%，确保有套利空间）
    min_samples         : 最低样本数（过滤数据不足的 symbol）
    limit               : 最多返回结果数

    Returns
    -------
    HTXScreenResult 包含排名结果
    """
    now = datetime.now(timezone.utc)

    if "htx" not in adapters:
        logger.warning("htx_screener_no_htx_adapter")
        return HTXScreenResult(generated_at=now, days_analyzed=days, symbols_screened=0)

    available_ref = [ex for ex in ref_exchanges if ex in adapters]
    if not available_ref:
        logger.warning("htx_screener_no_ref_adapters", ref_exchanges=ref_exchanges)
        return HTXScreenResult(generated_at=now, days_analyzed=days, symbols_screened=0)

    # ── 1. 获取候选 symbol 列表 ──────────────────────────────────────────────
    candidate_symbols = _get_candidate_symbols(hub, adapters, available_ref)
    if not candidate_symbols:
        return HTXScreenResult(generated_at=now, days_analyzed=days, symbols_screened=0)

    logger.info(
        "htx_screener_start",
        symbols=len(candidate_symbols),
        ref_exchanges=available_ref,
        days=days,
    )

    # ── 2. 拉 HTX + 所有可用 ref 的历史 funding ──────────────────────────────
    screen_adapters = {"htx": adapters["htx"]}
    for ex in available_ref:
        screen_adapters[ex] = adapters[ex]

    try:
        snaps = await load_funding_history(
            adapters=screen_adapters,
            symbols=candidate_symbols,
            days=days,
        )
    except Exception as e:
        logger.error("htx_screener_load_failed", error=str(e))
        return HTXScreenResult(generated_at=now, days_analyzed=days, symbols_screened=0)

    if not snaps:
        return HTXScreenResult(generated_at=now, days_analyzed=days, symbols_screened=0)

    # ── 3. 分组 ──────────────────────────────────────────────────────────────
    groups = _group_snaps(snaps)

    # ── 4. 对每个 symbol 计算指标 ────────────────────────────────────────────
    results: list[HTXSymbolResult] = []
    symbols_with_data = set()

    for sym in candidate_symbols:
        htx_key = ("htx", sym)
        htx_snaps = groups.get(htx_key)
        if not htx_snaps or len(htx_snaps) < min_samples:
            continue

        # 选数据最多的参考交易所
        best_ref: str | None = None
        best_ref_snaps: list[PerpFundingSnapshot] = []
        for ref_ex in available_ref:
            ref_key = (ref_ex, sym)
            ref_snaps = groups.get(ref_key, [])
            if len(ref_snaps) > len(best_ref_snaps):
                best_ref = ref_ex
                best_ref_snaps = ref_snaps

        if best_ref is None or len(best_ref_snaps) < min_samples:
            continue

        symbols_with_data.add(sym)
        metric = _compute_metrics(sym, best_ref, htx_snaps, best_ref_snaps)
        if metric is None:
            continue

        # 过滤低质量结果
        if (metric.sample_count < min_samples
                or float(metric.avg_diff_apr_pct) < min_avg_diff_apr
                or float(metric.persistence_pct) < min_persistence_pct):
            continue

        results.append(metric)

    # ── 5. 排序 + 截断 ───────────────────────────────────────────────────────
    results.sort(key=lambda r: r.score, reverse=True)
    if limit and len(results) > limit:
        results = results[:limit]

    logger.info(
        "htx_screener_complete",
        symbols_screened=len(symbols_with_data),
        qualified=len(results),
        days=days,
    )

    return HTXScreenResult(
        generated_at=now,
        days_analyzed=days,
        symbols_screened=len(symbols_with_data),
        results=results,
    )


def _get_candidate_symbols(
    hub: Any | None,
    adapters: dict[str, Any],
    available_ref: list[str],
) -> list[str]:
    """从 hub 缓存获取 HTX × ref_exchanges 的 symbol 交集。

    fallback: 如果 hub 没有足够数据，使用预置的宽 symbol 列表。
    """
    # 尝试从 hub 取
    if hub is not None:
        try:
            htx_syms = hub.known_funding_symbols("htx")
            ref_syms: set[str] = set()
            for ex in available_ref:
                ref_syms |= hub.known_funding_symbols(ex)
            overlap = htx_syms & ref_syms
            if len(overlap) >= 20:
                # 过滤非 USDT 对（避免噪声）
                return sorted(s for s in overlap if s.endswith("/USDT"))
        except Exception:
            pass

    # fallback: 宽覆盖 symbol 列表（主流 + 中盘）
    return sorted(_BROAD_SYMBOL_LIST)


# ---------------------------------------------------------------------------
# 宽覆盖 fallback symbol 列表（当 hub 数据不足时使用）
# ---------------------------------------------------------------------------
_BROAD_SYMBOL_LIST: set[str] = {
    # 主流
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT",
    "ADA/USDT", "TRX/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT", "NEAR/USDT",
    "UNI/USDT", "APT/USDT", "ARB/USDT", "OP/USDT", "SUI/USDT", "ATOM/USDT",
    "FIL/USDT", "AAVE/USDT", "INJ/USDT", "ICP/USDT", "RUNE/USDT", "SEI/USDT",
    "LDO/USDT", "TIA/USDT", "ETC/USDT", "PEPE/USDT", "WLD/USDT", "JUP/USDT",
    "DYDX/USDT", "CRV/USDT", "GMX/USDT", "SNX/USDT", "GRT/USDT",
    # 扩展筛选候选（游戏/娱乐/中盘 — HTX 溢价历史记录）
    "ENJ/USDT", "COMP/USDT", "SAND/USDT", "MANA/USDT", "AXS/USDT", "CHZ/USDT",
    "GALA/USDT", "IMX/USDT", "GODS/USDT", "YGG/USDT", "ALICE/USDT", "TLM/USDT",
    "LOKA/USDT", "PYR/USDT", "MAGIC/USDT", "VOXEL/USDT", "ROSE/USDT",
    "KSM/USDT", "KAVA/USDT", "ZIL/USDT", "HBAR/USDT", "IOTA/USDT",
    "ICX/USDT", "ONE/USDT", "CELO/USDT", "FTM/USDT", "WAVES/USDT",
    "EGLD/USDT", "THETA/USDT", "VET/USDT", "FLM/USDT", "CTSI/USDT",
    "SUSHI/USDT", "BAL/USDT", "REN/USDT", "BAND/USDT", "OGN/USDT",
    "OCEAN/USDT", "ANKR/USDT", "CHR/USDT", "CVC/USDT", "NKN/USDT",
    "DENT/USDT", "HOT/USDT", "MTL/USDT", "TOMO/USDT", "STMX/USDT",
    "FLUX/USDT", "PAXG/USDT", "WAXP/USDT", "JST/USDT", "SXP/USDT",
    "OMG/USDT", "BAT/USDT", "ZEC/USDT", "DASH/USDT", "EOS/USDT",
    "XMR/USDT", "NEO/USDT", "XLM/USDT", "QTUM/USDT", "ONT/USDT",
}
