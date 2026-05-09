"""metrics.py 滑窗 in-memory metrics 测试。"""
from __future__ import annotations

import time
from unittest.mock import patch

from app.core.metrics import MetricsRegistry, _Window, get_metrics, reset_metrics


class TestWindow:
    def test_empty_count(self):
        w = _Window()
        assert w.count() == 0

    def test_add_and_count(self):
        w = _Window()
        for _ in range(5):
            w.add()
        assert w.count() == 5

    def test_evicts_old_samples(self):
        """超过 5 分钟的样本应被淘汰。"""
        w = _Window()
        # 模拟 6 分钟前的样本
        with patch("app.core.metrics.time.monotonic", return_value=1000.0):
            w.add()
        # 当前时间 6 分 1 秒后
        with patch("app.core.metrics.time.monotonic", return_value=1000.0 + 361):
            assert w.count() == 0

    def test_percentile_p95(self):
        w = _Window()
        for v in range(1, 101):  # 1..100
            w.add(float(v))
        # p95 应接近 95
        p95 = w.percentile(95)
        assert 90 <= p95 <= 100

    def test_percentile_empty_returns_zero(self):
        assert _Window().percentile(95) == 0.0


class TestMetricsRegistry:
    def setup_method(self):
        reset_metrics()

    def teardown_method(self):
        reset_metrics()

    def test_singleton(self):
        a = get_metrics()
        b = get_metrics()
        assert a is b

    def test_record_http_2xx_no_error(self):
        m = MetricsRegistry()
        m.record_http(50.0, 200)
        assert m.http_requests.count() == 1
        assert m.http_errors.count() == 0
        assert m.http_error_rate_5m_pct() == 0.0

    def test_record_http_5xx_counted_as_error(self):
        m = MetricsRegistry()
        m.record_http(100.0, 500)
        m.record_http(50.0, 200)
        assert m.http_requests.count() == 2
        assert m.http_errors.count() == 1
        assert m.http_error_rate_5m_pct() == 50.0

    def test_record_http_4xx_counted_as_error(self):
        m = MetricsRegistry()
        m.record_http(20.0, 404)
        assert m.http_error_rate_5m_pct() == 100.0

    def test_record_ccxt_per_exchange(self):
        m = MetricsRegistry()
        m.record_ccxt("binance", ok=True)
        m.record_ccxt("binance", ok=True)
        m.record_ccxt("okx", ok=False)
        assert m.ccxt_call_count_5m("binance") == 2
        assert m.ccxt_call_count_5m("okx") == 1
        assert m.ccxt_error_rate_5m_pct("binance") == 0.0
        assert m.ccxt_error_rate_5m_pct("okx") == 100.0

    def test_record_scan_per_strategy(self):
        m = MetricsRegistry()
        m.record_scan("funding_rate", 250.0)
        m.record_scan("spot_perp", 30.0)
        assert m.scan_count_5m("funding_rate") == 1
        assert m.scan_count_5m("spot_perp") == 1
        assert m.scan_p95_ms("funding_rate") == 250.0
        assert m.scan_p95_ms("spot_perp") == 30.0

    def test_unknown_exchange_returns_zero(self):
        m = MetricsRegistry()
        assert m.ccxt_call_count_5m("xxx") == 0
        assert m.ccxt_error_rate_5m_pct("xxx") == 0.0

    def test_unknown_strategy_scan_p95_zero(self):
        m = MetricsRegistry()
        assert m.scan_p95_ms("foo") == 0.0
        assert m.scan_count_5m("foo") == 0

    def test_http_latency_p95(self):
        m = MetricsRegistry()
        for v in range(1, 101):
            m.record_http(float(v), 200)
        p95 = m.http_latency_p95_ms()
        assert 90 <= p95 <= 100
