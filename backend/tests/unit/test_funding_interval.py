"""funding_interval.py 单元测试 — 各交易所 ccxt 响应格式 → 周期推断。"""
from __future__ import annotations

from app.exchanges.cex.funding_interval import infer_funding_interval_hours


class TestInferFundingInterval:
    def test_default_when_empty(self):
        assert infer_funding_interval_hours(None) == 8
        assert infer_funding_interval_hours({}) == 8

    def test_default_override(self):
        assert infer_funding_interval_hours({}, default=4) == 4

    def test_unified_interval_string(self):
        # CCXT 部分版本提供 raw['interval'] = "8h" / "4h" / "1h"
        assert infer_funding_interval_hours({"interval": "8h"}) == 8
        assert infer_funding_interval_hours({"interval": "4h"}) == 4
        assert infer_funding_interval_hours({"interval": "1h"}) == 1
        assert infer_funding_interval_hours({"interval": "24h"}) == 24

    def test_binance_funding_interval_hours(self):
        # Binance: info.fundingIntervalHours
        raw = {"info": {"fundingIntervalHours": "4"}}
        assert infer_funding_interval_hours(raw) == 4

    def test_bitget_funding_interval_ms(self):
        # Bitget: info.fundingInterval 毫秒
        # 4h = 4 * 3600 * 1000 = 14400000
        raw = {"info": {"fundingInterval": 14_400_000}}
        assert infer_funding_interval_hours(raw) == 4
        # 8h = 28800000
        raw = {"info": {"fundingInterval": 28_800_000}}
        assert infer_funding_interval_hours(raw) == 8

    def test_okx_compute_from_timestamps(self):
        # OKX: 用 fundingTimestamp 与 info.prevFundingTime 差
        # 4h = 14_400_000 ms
        raw = {
            "fundingTimestamp": 1_700_000_000_000 + 14_400_000,
            "info": {"prevFundingTime": "1700000000000"},
        }
        assert infer_funding_interval_hours(raw) == 4

    def test_invalid_interval_falls_back(self):
        raw = {"interval": "garbage"}
        assert infer_funding_interval_hours(raw) == 8

    def test_invalid_ms_falls_back(self):
        raw = {"info": {"fundingInterval": "abc"}}
        assert infer_funding_interval_hours(raw) == 8

    def test_priority_unified_over_info(self):
        # unified interval 优先于 info 字段
        raw = {"interval": "4h", "info": {"fundingIntervalHours": "8"}}
        assert infer_funding_interval_hours(raw) == 4
