"""轻量级 in-memory metrics 收集器（无外部依赖）。

按需替代完整 Prometheus 栈：仅追踪运维关心的少量指标，5 分钟滑窗，
通过 ``/dashboard/summary`` 暴露给 UI。

收集的指标：
  - HTTP request count + error count + latency p50/p95（5 分钟滑窗）
  - Scanner scan duration p95（每个策略一个）
  - CCXT call count + error count（per exchange）

后续如需历史 / 告警 / 跨容器聚合，再换成 prometheus_client。
当前 2 策略 + 单容器规模，in-memory 足够。
"""
from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Deque


_WINDOW_SECONDS = 300  # 5 分钟滑窗


class _Window:
    """5 分钟滑窗的样本队列。每个样本 (timestamp, value)。"""

    __slots__ = ("_samples", "_lock")

    def __init__(self) -> None:
        self._samples: Deque[tuple[float, float]] = deque()
        self._lock = Lock()

    def add(self, value: float = 1.0) -> None:
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, value))
            self._evict(now)

    def _evict(self, now: float) -> None:
        cutoff = now - _WINDOW_SECONDS
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def count(self) -> int:
        with self._lock:
            self._evict(time.monotonic())
            return len(self._samples)

    def percentile(self, p: float) -> float:
        """返回滑窗内 value 的 p 分位数（p ∈ [0,100]），无样本时返回 0。"""
        with self._lock:
            self._evict(time.monotonic())
            if not self._samples:
                return 0.0
            values = sorted(v for _, v in self._samples)
            k = int(len(values) * p / 100)
            k = max(0, min(k, len(values) - 1))
            return values[k]


class MetricsRegistry:
    """所有运维指标的集中注册表（单例）。"""

    def __init__(self) -> None:
        # HTTP 入站请求
        self.http_requests = _Window()       # 全部请求 count
        self.http_errors = _Window()         # 5xx + 4xx
        self.http_latency_ms = _Window()     # 单次请求耗时（毫秒）
        # CCXT 出站调用（per exchange）
        self._ccxt_calls: dict[str, _Window] = {}
        self._ccxt_errors: dict[str, _Window] = {}
        # Scanner 扫描耗时（per strategy）
        self._scan_duration_ms: dict[str, _Window] = {}

    # ------------------------------------------------------------------
    # HTTP 入站
    # ------------------------------------------------------------------

    def record_http(self, latency_ms: float, status_code: int) -> None:
        self.http_requests.add()
        self.http_latency_ms.add(latency_ms)
        if status_code >= 400:
            self.http_errors.add()

    def http_error_rate_5m_pct(self) -> float:
        total = self.http_requests.count()
        if total == 0:
            return 0.0
        return (self.http_errors.count() / total) * 100.0

    def http_latency_p95_ms(self) -> float:
        return self.http_latency_ms.percentile(95)

    # ------------------------------------------------------------------
    # CCXT 出站
    # ------------------------------------------------------------------

    def record_ccxt(self, exchange: str, ok: bool) -> None:
        ex = (exchange or "unknown").lower()
        self._ccxt_calls.setdefault(ex, _Window()).add()
        if not ok:
            self._ccxt_errors.setdefault(ex, _Window()).add()

    def ccxt_call_count_5m(self, exchange: str) -> int:
        return self._ccxt_calls.get(exchange.lower(), _Window()).count()

    def ccxt_error_rate_5m_pct(self, exchange: str) -> float:
        ex = exchange.lower()
        total = self._ccxt_calls.get(ex, _Window()).count()
        if total == 0:
            return 0.0
        errs = self._ccxt_errors.get(ex, _Window()).count()
        return (errs / total) * 100.0

    # ------------------------------------------------------------------
    # Scanner 性能
    # ------------------------------------------------------------------

    def record_scan(self, strategy: str, duration_ms: float) -> None:
        self._scan_duration_ms.setdefault(strategy, _Window()).add(duration_ms)

    def scan_p95_ms(self, strategy: str) -> float:
        return self._scan_duration_ms.get(strategy, _Window()).percentile(95)

    def scan_count_5m(self, strategy: str) -> int:
        return self._scan_duration_ms.get(strategy, _Window()).count()


# 单例
_REGISTRY: MetricsRegistry | None = None


def get_metrics() -> MetricsRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = MetricsRegistry()
    return _REGISTRY


def reset_metrics() -> None:
    """仅测试用。"""
    global _REGISTRY
    _REGISTRY = MetricsRegistry()
