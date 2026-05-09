"""推断 funding rate 周期（小时）— 共享辅助。

各交易所 funding 周期：
  Binance USDM:  默认 8h；部分 alts (DOGE/SHIB 等) 4h
  OKX swap:      默认 8h；某些标的 4h
  Bitget mix:    主流 8h；多数 alts 4h（BILL 案例）
  Bybit linear:  默认 8h
  HTX swap:      默认 8h

CCXT 在不同 exchange 用不同字段返回 interval，此模块封装识别逻辑。
"""
from __future__ import annotations


_DEFAULT_HOURS = 8


def infer_funding_interval_hours(raw: dict | None, default: int = _DEFAULT_HOURS) -> int:
    """从 CCXT fetch_funding_rate 响应推断 funding 周期（小时）。

    优先级：
      1. raw['interval'] / raw['info']['fundingIntervalHours']  (Binance 直接给)
      2. raw['fundingTimestamp'] - prev_funding_time（如 info 里有）
      3. info['fundingInterval']（Bitget 返回毫秒）
      4. fallback 到 default（8h）
    """
    if not raw:
        return default
    info = (raw.get("info") or {}) if isinstance(raw, dict) else {}

    # 1. CCXT unified interval（部分版本提供）
    iv = raw.get("interval")
    if isinstance(iv, str):
        # 如 "8h" / "4h" / "1h"
        s = iv.strip().lower()
        if s.endswith("h"):
            try:
                return int(float(s[:-1]))
            except ValueError:
                pass

    # 2. Binance: info.fundingIntervalHours
    h = info.get("fundingIntervalHours")
    if h is not None:
        try:
            return int(float(h))
        except (TypeError, ValueError):
            pass

    # 3. Bitget: info.fundingInterval（毫秒）
    iv_ms = info.get("fundingInterval")
    if iv_ms is not None:
        try:
            ms = int(iv_ms)
            if ms > 0:
                return max(1, ms // (3600 * 1000))
        except (TypeError, ValueError):
            pass

    # 4. OKX: info.fundingTime - info.nextFundingTime / 类似算
    next_ts = raw.get("fundingTimestamp")
    prev_ts = info.get("prevFundingTime") or info.get("settleTime")
    if next_ts and prev_ts:
        try:
            delta_ms = int(next_ts) - int(prev_ts)
            if delta_ms > 0:
                hours = delta_ms / (3600 * 1000)
                # 取最近的整数小时（1/2/4/8/24）
                for candidate in (1, 2, 4, 8, 24):
                    if abs(hours - candidate) < 0.5:
                        return candidate
        except (TypeError, ValueError):
            pass

    return default
