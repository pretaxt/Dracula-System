"""Scanner API — GET /scanner/htx-premium

HTX 结构性溢价 symbol 筛选端点。
拉 N 天历史 funding rate，计算 HTX vs 参考所的 diff 持续性，排名输出。

⚠️ 该端点会实时拉取交易所历史数据，首次调用需 30-60s。
建议调用时传 ?days=7 快速验证，正式分析用 days=14。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.strategies.perp_basis.htx_screener import (
    HTXScreenResult,
    HTXSymbolResult,
    screen_htx_premium,
)

router = APIRouter(prefix="/scanner", tags=["scanner"])


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------


class SymbolResultOut(BaseModel):
    symbol: str
    ref_exchange: str
    avg_diff_apr_pct: str
    persistence_pct: str
    stddev_diff_apr_pct: str
    score: str
    sample_count: int
    max_diff_apr_pct: str
    last_diff_apr_pct: str
    htx_avg_apr_pct: str
    ref_avg_apr_pct: str
    break_even_hours: str
    recommendation: str


class HTXPremiumResponse(BaseModel):
    generated_at: str
    days_analyzed: int
    symbols_screened: int
    elapsed_seconds: float
    results: list[SymbolResultOut]


# ---------------------------------------------------------------------------
# 内存缓存（单机进程内，TTL 6h）
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {}
_CACHE_TTL_SECS = 6 * 3600


def _cache_key(days: int) -> str:
    """Cache only on days — filter params applied at response time."""
    return f"htx_premium:{days}"


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/htx-premium", response_model=HTXPremiumResponse)
async def get_htx_premium(
    request: Request,
    days: int = Query(default=14, ge=7, le=30, description="分析历史天数"),
    min_persistence_pct: float = Query(
        default=40.0, ge=10.0, le=95.0,
        description="HTX 为高 funding 端的最低时间占比 (%)",
    ),
    min_avg_diff_apr: float = Query(
        default=15.0, ge=1.0, le=200.0,
        description="最低平均 APR 差 (%)",
    ),
    min_samples: int = Query(default=8, ge=3, le=50, description="最低样本数"),
    limit: int = Query(default=30, ge=1, le=100, description="返回 symbol 数量上限"),
    force_refresh: bool = Query(default=False, description="忽略缓存，强制重新拉取"),
) -> HTXPremiumResponse:
    """HTX 结构性溢价 symbol 筛选。

    返回按 score（avg_diff × persistence）降序排列的 symbol 列表。
    score 高 = HTX 长期为高 funding 端 → 可以在 HTX SHORT + 参考所 LONG。

    **首次调用约 30-60 秒**（实时拉取交易所历史数据）。
    6 小时内相同参数会命中缓存，秒级返回。
    """
    adapters: dict[str, Any] = getattr(request.app.state, "adapters", None) or {}
    hub = getattr(request.app.state, "market_data_hub", None)

    if "htx" not in adapters:
        raise HTTPException(status_code=503, detail="HTX 适配器未就绪")

    # 缓存命中（只按 days 区分；filter 参数在返回时再应用）
    ck = _cache_key(days)
    if not force_refresh and ck in _cache:
        cached = _cache[ck]
        if time.monotonic() - cached["ts"] < _CACHE_TTL_SECS:
            result: HTXScreenResult = cached["result"]
            filtered = _apply_filters(
                result.results, min_persistence_pct, min_avg_diff_apr, min_samples, limit,
            )
            return _to_response(result, filtered, cached["elapsed"])

    # 实时拉取 — 用宽松阈值捞全量，缓存后按请求参数过滤
    t0 = time.monotonic()
    result = await screen_htx_premium(
        adapters=adapters,
        hub=hub,
        days=days,
        min_persistence_pct=0.0,   # 宽松：不在 screener 层过滤
        min_avg_diff_apr=0.0,      # 宽松：留给 API 层过滤
        min_samples=min_samples,
        limit=200,                  # 捞足够多，缓存复用
    )
    elapsed = time.monotonic() - t0

    # 存缓存
    _cache[ck] = {"result": result, "ts": time.monotonic(), "elapsed": elapsed}

    filtered = _apply_filters(
        result.results, min_persistence_pct, min_avg_diff_apr, min_samples, limit,
    )
    return _to_response(result, filtered, elapsed)


def _apply_filters(
    rows: list,
    min_persistence_pct: float,
    min_avg_diff_apr: float,
    min_samples: int,
    limit: int,
) -> list:
    """对已排序的 screener 结果二次过滤 + 截断。"""
    out = [
        r for r in rows
        if (float(r.persistence_pct) >= min_persistence_pct
            and float(r.avg_diff_apr_pct) >= min_avg_diff_apr
            and r.sample_count >= min_samples)
    ]
    return out[:limit]


def _to_response(
    result: HTXScreenResult,
    rows: list[HTXSymbolResult],
    elapsed: float,
) -> HTXPremiumResponse:
    return HTXPremiumResponse(
        generated_at=result.generated_at.isoformat(),
        days_analyzed=result.days_analyzed,
        symbols_screened=result.symbols_screened,
        elapsed_seconds=round(elapsed, 2),
        results=[
            SymbolResultOut(
                symbol=r.symbol,
                ref_exchange=r.ref_exchange,
                avg_diff_apr_pct=r.to_dict()["avg_diff_apr_pct"],
                persistence_pct=r.to_dict()["persistence_pct"],
                stddev_diff_apr_pct=r.to_dict()["stddev_diff_apr_pct"],
                score=r.to_dict()["score"],
                sample_count=r.sample_count,
                max_diff_apr_pct=r.to_dict()["max_diff_apr_pct"],
                last_diff_apr_pct=r.to_dict()["last_diff_apr_pct"],
                htx_avg_apr_pct=r.to_dict()["htx_avg_apr_pct"],
                ref_avg_apr_pct=r.to_dict()["ref_avg_apr_pct"],
                break_even_hours=r.to_dict()["break_even_hours"],
                recommendation=r.recommendation,
            )
            for r in rows
        ],
    )
