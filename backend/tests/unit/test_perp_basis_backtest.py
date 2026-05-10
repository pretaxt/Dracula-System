"""单元测试 — backtest/perp_basis_engine.py + models。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.backtest.perp_basis_engine import run_perp_basis_backtest
from app.backtest.perp_basis_models import (
    PerpBasisBacktestConfig,
    PerpFundingSnapshot,
)


def _snap(ts, sym, ex, rate, price="100", interval=8):
    return PerpFundingSnapshot(
        timestamp=ts, symbol=sym, exchange=ex,
        funding_rate=Decimal(str(rate)),
        funding_interval_hours=interval,
        perp_price=Decimal(str(price)),
    )


T0 = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)


class TestModelsApr:
    def test_apr_8h_period(self):
        s = _snap(T0, "FIL/USDT", "binance", "0.0001", interval=8)
        # rate × periods/year × 100 = 0.0001 × 1095 × 100 = 10.95
        assert abs(float(s.apr_pct) - 10.95) < 0.01

    def test_apr_4h_period(self):
        s = _snap(T0, "FIL/USDT", "binance", "0.0001", interval=4)
        assert abs(float(s.apr_pct) - 21.9) < 0.01


class TestEngineEmpty:
    def test_no_snapshots_returns_initial_equity(self):
        cfg = PerpBasisBacktestConfig()
        r = run_perp_basis_backtest([], cfg)
        assert r.final_equity_usd == cfg.initial_capital_usd
        assert r.num_trades == 0


class TestEngineNoSpread:
    def test_no_diff_no_trade(self):
        cfg = PerpBasisBacktestConfig(min_diff_apr_pct=Decimal("30"))
        # 两个交易所 funding 一样 → diff=0 → 不开仓
        snaps = []
        for h in range(0, 8 * 5, 8):  # 5 个周期
            ts = T0 + timedelta(hours=h)
            snaps.append(_snap(ts, "FIL/USDT", "binance", "0.0001"))
            snaps.append(_snap(ts, "FIL/USDT", "okx", "0.0001"))
        r = run_perp_basis_backtest(snaps, cfg)
        assert r.num_trades == 0
        assert r.final_equity_usd == cfg.initial_capital_usd


class TestEngineSpreadOpens:
    def test_diff_above_threshold_opens_trade(self):
        cfg = PerpBasisBacktestConfig(
            min_diff_apr_pct=Decimal("30"),
            min_hold_hours=Decimal("0"),
            exit_diff_apr_pct=Decimal("5"),
            initial_capital_usd=Decimal("1000"),
            notional_per_position=Decimal("100"),
            fee_rate=Decimal("0"),  # 暂忽略 fee 简化断言
            slippage_pct=Decimal("0"),
        )
        snaps = []
        # 第 1 周期：binance APR 50%, okx APR 5% → diff 45 > 30 → 开仓
        # binance funding rate 0.000457 → APR ~50%（8h period × 1095 × 100 = 50.04）
        snaps.append(_snap(T0, "FIL/USDT", "binance", "0.000457"))
        snaps.append(_snap(T0, "FIL/USDT", "okx", "0.0000457"))
        # 第 2 周期：差值仍大 → 持仓收 funding
        ts2 = T0 + timedelta(hours=8)
        snaps.append(_snap(ts2, "FIL/USDT", "binance", "0.000457"))
        snaps.append(_snap(ts2, "FIL/USDT", "okx", "0.0000457"))
        # 第 3 周期：差值降 → 退出
        ts3 = T0 + timedelta(hours=16)
        snaps.append(_snap(ts3, "FIL/USDT", "binance", "0.00001"))
        snaps.append(_snap(ts3, "FIL/USDT", "okx", "0.00001"))

        r = run_perp_basis_backtest(snaps, cfg)
        assert r.num_trades >= 1
        trade = r.trades[0]
        assert trade.long_exchange == "okx"  # apr 低
        assert trade.short_exchange == "binance"  # apr 高
        assert trade.funding_collected > 0  # 收 funding 差


class TestEngineMaxConcurrent:
    def test_max_concurrent_limit_respected(self):
        cfg = PerpBasisBacktestConfig(
            min_diff_apr_pct=Decimal("30"),
            max_concurrent=1,
            min_hold_hours=Decimal("100"),  # 不退出
            fee_rate=Decimal("0"),
        )
        ts = T0
        snaps = [
            _snap(ts, "FIL/USDT", "binance", "0.000457"),
            _snap(ts, "FIL/USDT", "okx", "0.0000457"),
            _snap(ts, "ETH/USDT", "binance", "0.000457"),
            _snap(ts, "ETH/USDT", "okx", "0.0000457"),
        ]
        r = run_perp_basis_backtest(snaps, cfg)
        # 只允许 1 笔同时，2 个机会但只开 1 笔
        # 由于回测 strict mode，所有 trade 在结束被 force_close — 只 1 笔
        assert len(r.trades) == 1


class TestLoaderMinVolumeFilter:
    """min_volume_24h 过滤防小币种脏数据。"""

    @pytest.mark.asyncio
    async def test_volume_filter_drops_below_threshold(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.backtest.perp_basis_loader import _fetch_volume_filter

        adapter = MagicMock()
        perp_client = MagicMock()
        # 模拟 ticker：FIL volume 5M < 10M threshold；BTC volume 100M > 阈值
        async def fake_fetch_ticker(ccxt_sym):
            if "FIL" in ccxt_sym:
                return {"quoteVolume": 5_000_000}
            return {"quoteVolume": 100_000_000}
        perp_client.fetch_ticker = AsyncMock(side_effect=fake_fetch_ticker)
        from app.exchanges.models import InstrumentType
        adapter._clients = {InstrumentType.PERPETUAL: perp_client}

        valid = await _fetch_volume_filter(
            {"binance": adapter},
            ["FIL/USDT", "BTC/USDT"],
            Decimal("10000000"),
        )
        assert ("binance", "BTC/USDT") in valid
        assert ("binance", "FIL/USDT") not in valid


class TestEngineExtremeFiltered:
    def test_max_abs_apr_filters_dirty_data(self):
        """TIA HTX -99% APR 这种极端值应被 max_abs_apr_pct 过滤掉。"""
        cfg = PerpBasisBacktestConfig(
            min_diff_apr_pct=Decimal("30"),
            max_abs_apr_pct=Decimal("500"),
        )
        ts = T0
        # binance 50%、okx 5%、htx -1000%（脏数据）
        snaps = [
            _snap(ts, "TIA/USDT", "binance", "0.000457"),
            _snap(ts, "TIA/USDT", "okx", "0.0000457"),
            _snap(ts, "TIA/USDT", "htx", "-0.01"),  # APR ~ -1095% 远超 cap
        ]
        # htx 应被过滤；保留 binance/okx 配对，diff ≈ 45% > 30
        r = run_perp_basis_backtest(snaps, cfg)
        # htx 被过滤后剩 binance(short) + okx(long)
        if r.trades:
            t = r.trades[0]
            assert "htx" not in (t.long_exchange, t.short_exchange)
