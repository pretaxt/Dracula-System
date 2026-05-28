"""
dgr_btc/regime_detector.py — §6.2 #8 regime detector

quant agent 审查指出: dgr_btc 在 sideways-with-deep-drawdown regime (如 2024 H2)
会被 grind down. 需 detector 进入此 regime 时暂停加仓 (ADD_LAYER + 新 cycle ENTRY),
让现有持仓自然走 TP/SL.

阈值 (quant agent 建议):
  - 30d realized vol < 25% (annualized)
  - AND 30d max DD > -15% (即从 30d peak 跌 < 15%)

设计原则:
  - 无状态可重入: 每次调用传入 30d 价格序列, 函数算出当前 regime
  - 不引外部依赖 (用 stdlib + math.sqrt)
  - 容错: 数据不足 warmup_days 时返回 normal regime (不触发 gate)
  - paper 和 backtest 用同一函数 → mirror byte-equal

调用方:
  paper_trading._tick 拉 30d 1h bars → compute_metrics → is_bad_regime →
  传给 engine.decide(..., allow_add_layer=not is_bad_regime)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence


@dataclass(frozen=True)
class RegimeMetrics:
    """30 日窗口算出的市场体检指标"""
    realized_vol_annualized: float  # 年化波动率, 0.30 = 30%/year
    max_drawdown_pct: float  # 从窗口内 peak 的跌幅, 负数, -0.15 = 跌 15%
    sample_size: int  # 实际计算用的日数 (>= warmup 才有效)
    is_bad: bool  # vol < vol_threshold 且 dd_pct > dd_threshold 时为 True


def compute_regime_metrics(
    daily_closes: Sequence[float | Decimal],
    *,
    vol_threshold: float = 0.25,
    dd_threshold: float = -0.15,
    warmup_days: int = 20,
) -> RegimeMetrics:
    """从 30 日 daily close 序列算 regime 指标.

    Args:
      daily_closes: 最近 N 日 daily close, 时间升序. 推荐传 30 日.
      vol_threshold: 低波阈值 (年化). 默认 25% = 0.25.
      dd_threshold: 浅回撤阈值 (从 peak 跌幅). 默认 -15% = -0.15.
        注意: dd_pct 是负数 (-0.10 = 跌 10%), 阈值也是负数.
        is_bad 判定: dd_pct > dd_threshold 表示"回撤还没超过阈值",
        即处于 sideways-with-shallow-drawdown 危险区.
      warmup_days: 数据不足此天数时 is_bad=False (保险路径).

    Returns:
      RegimeMetrics dataclass.

    Note:
      - 用 daily close 算 log return → 年化 vol = std(returns) * sqrt(252).
        252 个交易日 = 传统股票惯例; crypto 是 365 日 24h 也可, 但 252 与
        多数文献一致, 不影响 regime 触发的 directional 信号.
      - dd 用 cummax-relative drawdown 算 (走过窗口内每个点回头看 peak).
    """
    n = len(daily_closes)
    if n < 2:
        return RegimeMetrics(0.0, 0.0, n, False)

    closes = [float(c) for c in daily_closes]

    # ─── realized vol (年化, 用 log returns) ───
    log_rets = []
    for i in range(1, n):
        prev = closes[i - 1]
        cur = closes[i]
        if prev <= 0 or cur <= 0:
            continue
        log_rets.append(math.log(cur / prev))

    if not log_rets:
        return RegimeMetrics(0.0, 0.0, n, False)

    mean_r = sum(log_rets) / len(log_rets)
    var = sum((r - mean_r) ** 2 for r in log_rets) / max(1, len(log_rets) - 1)
    daily_vol = math.sqrt(var)
    realized_vol_annualized = daily_vol * math.sqrt(252)

    # ─── max drawdown from rolling peak ───
    peak = closes[0]
    max_dd_pct = 0.0
    for c in closes:
        if c > peak:
            peak = c
        if peak > 0:
            dd = (c - peak) / peak
            if dd < max_dd_pct:
                max_dd_pct = dd

    # ─── is_bad: 低 vol + 浅回撤 = sideways-grinder 危险区 ───
    # warmup 期内不触发 (避免冷启动时数据不足导致误判)
    if n < warmup_days:
        is_bad = False
    else:
        # vol 低于阈值 AND 回撤还没足够深 (dd_pct > dd_threshold = 跌幅不够大)
        is_bad = (
            realized_vol_annualized < vol_threshold
            and max_dd_pct > dd_threshold
        )

    return RegimeMetrics(
        realized_vol_annualized=realized_vol_annualized,
        max_drawdown_pct=max_dd_pct,
        sample_size=n,
        is_bad=is_bad,
    )


def closes_from_klines(klines: Sequence) -> list[float]:
    """从 kline 对象列表 (有 .close 属性) 提取 daily close.

    若输入是 1h bars, 调用方应先做日聚合 (取每天最后一根).
    """
    out: list[float] = []
    for k in klines:
        c = getattr(k, "close", None)
        if c is None:
            c = k.get("close") if isinstance(k, dict) else None
        if c is None:
            continue
        try:
            out.append(float(c))
        except (TypeError, ValueError):
            continue
    return out


def aggregate_hourly_to_daily(hourly_closes: Sequence[float], *, bars_per_day: int = 24) -> list[float]:
    """聚合 1h close 到 daily close (取每日最后一根).

    输入: 1h close 序列 (升序). 输出: daily close (每 24 根取 1, 末尾保留余数).
    """
    if not hourly_closes:
        return []
    closes = [float(c) for c in hourly_closes]
    n = len(closes)
    daily: list[float] = []
    # 从头取每 24 个的最后一个 (24 整数倍 bars 完整覆盖)
    for end in range(bars_per_day - 1, n, bars_per_day):
        daily.append(closes[end])
    # 末尾若有不足 bars_per_day 的"零头", 单独补最后一根
    remainder = n % bars_per_day
    if remainder != 0:
        daily.append(closes[-1])
    return daily
