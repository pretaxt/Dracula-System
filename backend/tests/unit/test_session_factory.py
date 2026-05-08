"""单元测试 — strategies/funding_rate/session_factory.py"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.exchanges.base import ExchangeAdapter
from app.exchanges.models import Symbol
from app.execution.live_broker import LiveBroker
from app.execution.paper_broker import PaperBroker
from app.risk.limits import RiskLimits
from app.strategies.funding_rate.paper_trading import PaperTradingSession
from app.strategies.funding_rate.scanner import FundingRateScanner
from app.strategies.funding_rate.session_factory import build_paper_session

BTC = Symbol("BTC", "USDT")
ETH = Symbol("ETH", "USDT")
_SYMBOLS = [BTC, ETH]


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _mock_adapters() -> dict:
    return {"binance": MagicMock(spec=ExchangeAdapter)}


def _cfg(**overrides) -> dict:
    """构造最小合法配置，支持按节覆盖（传 None 表示删除该节）。"""
    base: dict = {
        "instance_name": "test_instance",
        "position": {"max_positions": 2, "size_usd": 300},
        "execution": {"slippage_bps": 5, "fee_rate": "0.001"},
        "risk": {"stop_loss_pct": "1.5", "max_total_notional_usd": "5000"},
        "entry": {"min_apr_pct": "12.0"},
        "exit": {"max_hold_hours": "480"},
    }
    for section, value in overrides.items():
        if value is None:
            base.pop(section, None)
        else:
            base[section] = value
    return base


# ---------------------------------------------------------------------------
# 返回类型 & 基础行为
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_returns_paper_trading_session(self):
        session = build_paper_session(_cfg(), _mock_adapters(), _SYMBOLS)
        assert isinstance(session, PaperTradingSession)

    def test_scanner_is_funding_rate_scanner(self):
        session = build_paper_session(_cfg(), _mock_adapters(), _SYMBOLS)
        assert isinstance(session._scanner, FundingRateScanner)

    def test_empty_adapters_still_returns_session(self):
        """适配器为空时工厂仍能创建会话（扫描时自然返回空列表）。"""
        session = build_paper_session(_cfg(), {}, _SYMBOLS)
        assert isinstance(session, PaperTradingSession)

    def test_empty_symbols_still_returns_session(self):
        session = build_paper_session(_cfg(), _mock_adapters(), [])
        assert isinstance(session, PaperTradingSession)


# ---------------------------------------------------------------------------
# 执行层 — size / interval / instance_name
# ---------------------------------------------------------------------------


class TestExecutionWiring:
    def test_size_usd_applied(self):
        session = build_paper_session(_cfg(), _mock_adapters(), _SYMBOLS)
        assert session._size == Decimal("300")

    def test_default_size_usd_when_missing(self):
        session = build_paper_session(_cfg(position={}), _mock_adapters(), _SYMBOLS)
        assert session._size == Decimal("500")

    def test_scan_interval_passed_through(self):
        session = build_paper_session(
            _cfg(), _mock_adapters(), _SYMBOLS, scan_interval_seconds=30.0
        )
        assert session._interval == 30.0

    def test_default_scan_interval_is_60(self):
        session = build_paper_session(_cfg(), _mock_adapters(), _SYMBOLS)
        assert session._interval == 60.0

    def test_strategy_instance_name_applied(self):
        session = build_paper_session(_cfg(), _mock_adapters(), _SYMBOLS)
        assert session._executor._strategy_instance == "test_instance"

    def test_default_instance_name_when_missing(self):
        cfg = _cfg()
        cfg.pop("instance_name")
        session = build_paper_session(cfg, _mock_adapters(), _SYMBOLS)
        assert session._executor._strategy_instance == "funding_rate_main"


# ---------------------------------------------------------------------------
# Broker — slippage / fee
# ---------------------------------------------------------------------------


class TestBrokerWiring:
    def _broker(self, cfg) -> PaperBroker:
        return build_paper_session(cfg, _mock_adapters(), _SYMBOLS)._executor._broker

    def test_slippage_bps_applied(self):
        assert self._broker(_cfg()).slippage_bps == Decimal("5")

    def test_fee_rate_applied(self):
        assert self._broker(_cfg()).fee_rate == Decimal("0.001")

    def test_default_slippage_when_execution_empty(self):
        assert self._broker(_cfg(execution={})).slippage_bps == Decimal("2")

    def test_default_fee_rate_when_execution_empty(self):
        assert self._broker(_cfg(execution={})).fee_rate == Decimal("0.0004")

    def test_default_slippage_when_section_absent(self):
        assert self._broker(_cfg(execution=None)).slippage_bps == Decimal("2")


# ---------------------------------------------------------------------------
# 风控参数
# ---------------------------------------------------------------------------


class TestRiskLimitsWiring:
    def _limits(self, cfg) -> RiskLimits:
        return build_paper_session(cfg, _mock_adapters(), _SYMBOLS)._executor._guard.limits

    def test_max_positions_from_position_section(self):
        assert self._limits(_cfg()).max_positions == 2

    def test_default_max_positions_when_position_empty(self):
        assert self._limits(_cfg(position={})).max_positions == 3

    def test_stop_loss_from_risk_section(self):
        assert self._limits(_cfg()).stop_loss_pct == Decimal("1.5")

    def test_default_stop_loss_when_risk_empty(self):
        assert self._limits(_cfg(risk={})).stop_loss_pct == Decimal("2.0")

    def test_max_hold_hours_from_exit_section(self):
        assert self._limits(_cfg()).max_hold_hours == Decimal("480")

    def test_max_hold_hours_fallback_to_risk_section(self):
        """exit 节缺失时回落到 risk.max_hold_hours。"""
        cfg = _cfg(exit={}, risk={"max_hold_hours": "360"})
        assert self._limits(cfg).max_hold_hours == Decimal("360")

    def test_max_hold_hours_default_when_both_missing(self):
        assert self._limits(_cfg(exit=None, risk={})).max_hold_hours == Decimal("720")

    def test_min_apr_from_entry_section(self):
        assert self._limits(_cfg()).min_apr_pct == Decimal("12.0")

    def test_default_min_apr_when_entry_empty(self):
        assert self._limits(_cfg(entry={})).min_apr_pct == Decimal("10.0")

    def test_max_total_notional_from_risk_section(self):
        assert self._limits(_cfg()).max_total_notional_usd == Decimal("5000")

    def test_all_defaults_when_config_is_empty(self):
        limits = self._limits({})
        assert limits.max_positions == 3
        assert limits.stop_loss_pct == Decimal("2.0")
        assert limits.max_hold_hours == Decimal("720")
        assert limits.min_apr_pct == Decimal("10.0")
        assert limits.max_total_notional_usd == Decimal("10000")


# ---------------------------------------------------------------------------
# 杠杆参数 — leverage.default → executor._perp_leverage
# ---------------------------------------------------------------------------


class TestLeverageWiring:
    def _executor(self, cfg):
        return build_paper_session(cfg, _mock_adapters(), _SYMBOLS)._executor

    def test_leverage_default_propagates_to_executor(self):
        cfg = _cfg(leverage={"default": "5", "max": "5"})
        assert self._executor(cfg)._perp_leverage == Decimal("5")

    def test_default_leverage_when_section_missing(self):
        """无 leverage 节 → 默认 1（无杠杆）。"""
        cfg = _cfg()
        cfg.pop("leverage", None)
        assert self._executor(cfg)._perp_leverage == Decimal("1")

    def test_default_leverage_when_section_empty(self):
        cfg = _cfg(leverage={})
        assert self._executor(cfg)._perp_leverage == Decimal("1")

    def test_leverage_accepts_numeric_string(self):
        """YAML 里的 numeric / string 都能解析。"""
        cfg = _cfg(leverage={"default": 3})
        assert self._executor(cfg)._perp_leverage == Decimal("3")


# ---------------------------------------------------------------------------
# 退出参数 — exit / risk 节 → session 字段
# ---------------------------------------------------------------------------


class TestExitParamsWiring:
    def _session(self, cfg):
        return build_paper_session(cfg, _mock_adapters(), _SYMBOLS)

    def test_pre_funding_window_minutes_applied(self):
        cfg = _cfg(entry={"pre_funding_window_minutes": 20.0, "min_apr_pct": "12.0"})
        assert self._session(cfg)._pre_funding_window_min == 20.0

    def test_default_pre_funding_window_is_15(self):
        cfg = _cfg(entry={"min_apr_pct": "12.0"})
        assert self._session(cfg)._pre_funding_window_min == 15.0

    def test_min_apr_for_hold_pct_applied(self):
        cfg = _cfg(exit={"min_apr_for_hold_pct": "8.0"})
        assert self._session(cfg)._min_apr_for_hold == Decimal("8.0")

    def test_default_min_apr_for_hold_is_zero(self):
        cfg = _cfg(exit={})
        assert self._session(cfg)._min_apr_for_hold == Decimal("0")

    def test_profit_target_pct_applied(self):
        cfg = _cfg(exit={"profit_target_pct": "10"})
        assert self._session(cfg)._profit_target_pct == Decimal("10")

    def test_default_profit_target_is_zero(self):
        cfg = _cfg(exit={})
        assert self._session(cfg)._profit_target_pct == Decimal("0")

    def test_perp_margin_loss_threshold_applied(self):
        cfg = _cfg(risk={"perp_margin_loss_threshold_pct": "80",
                         "stop_loss_pct": "1.5",
                         "max_total_notional_usd": "5000"})
        assert self._session(cfg)._perp_margin_loss_threshold == Decimal("80")

    def test_default_perp_margin_threshold_is_zero(self):
        """缺省 → 0 = 不检查（向后兼容老配置）。"""
        cfg = _cfg(risk={"stop_loss_pct": "1.5", "max_total_notional_usd": "5000"})
        assert self._session(cfg)._perp_margin_loss_threshold == Decimal("0")


# ---------------------------------------------------------------------------
# Live mode — broker 选择 + 必备依赖
# ---------------------------------------------------------------------------


class TestLiveModeWiring:
    def test_live_mode_returns_live_broker(self):
        cfg = _cfg(leverage={"default": "5"})
        session = build_paper_session(cfg, _mock_adapters(), _SYMBOLS, live_mode=True)
        assert isinstance(session._executor._broker, LiveBroker)

    def test_paper_mode_default_returns_paper_broker(self):
        session = build_paper_session(_cfg(), _mock_adapters(), _SYMBOLS)
        assert isinstance(session._executor._broker, PaperBroker)

    def test_live_mode_without_binance_adapter_raises(self):
        """live_mode=True 但 adapters 里没有 'binance' → 立即 RuntimeError，避免静默 fallback。"""
        with pytest.raises(RuntimeError, match="binance"):
            build_paper_session(_cfg(), {}, _SYMBOLS, live_mode=True)

    def test_live_broker_receives_perp_leverage(self):
        cfg = _cfg(leverage={"default": "5"})
        session = build_paper_session(cfg, _mock_adapters(), _SYMBOLS, live_mode=True)
        broker = session._executor._broker
        assert isinstance(broker, LiveBroker)
        assert broker._perp_leverage == Decimal("5")

    def test_live_broker_receives_fee_rate(self):
        cfg = _cfg(execution={"slippage_bps": 5, "fee_rate": "0.0002"})
        session = build_paper_session(cfg, _mock_adapters(), _SYMBOLS, live_mode=True)
        broker = session._executor._broker
        assert isinstance(broker, LiveBroker)
        assert broker.fee_rate == Decimal("0.0002")
