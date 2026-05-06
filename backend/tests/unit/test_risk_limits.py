"""单元测试 — risk/limits.py（风控规则引擎）"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.exchanges.models import Symbol
from app.risk.limits import RiskGuard, RiskLimitError, RiskLimits, RiskViolation
from app.risk.models import ExitReason, Position, PositionStatus

BTC = Symbol("BTC", "USDT")
ETH = Symbol("ETH", "USDT")


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_open_position(notional: str = "500", symbol: Symbol = BTC) -> Position:
    pos = Position(
        strategy_instance="funding_rate_main",
        symbol=symbol,
        notional_usd=Decimal(notional),
    )
    pos.mark_open()
    return pos


def _default_guard(**overrides) -> RiskGuard:
    limits = RiskLimits(
        max_positions=3,
        max_total_notional_usd=Decimal("3000"),
        max_position_size_usd=Decimal("1000"),
        min_position_size_usd=Decimal("50"),
        stop_loss_pct=Decimal("2.0"),
        max_hold_hours=Decimal("168"),
        min_apr_pct=Decimal("10.0"),
    )
    for k, v in overrides.items():
        setattr(limits, k, v)
    return RiskGuard(limits=limits)


# ---------------------------------------------------------------------------
# RiskLimits.from_yaml
# ---------------------------------------------------------------------------


class TestRiskLimitsFromYaml:
    def test_defaults_when_empty_dict(self):
        limits = RiskLimits.from_yaml({})
        assert limits.max_positions == 5
        assert limits.stop_loss_pct == Decimal("2.0")
        assert limits.min_apr_pct == Decimal("10.0")

    def test_overrides_from_yaml(self):
        cfg = {
            "risk": {
                "max_positions": 2,
                "stop_loss_pct": 1.5,
                "max_total_notional_usd": 5000,
            },
            "entry": {"min_apr_pct": 15.0},
        }
        limits = RiskLimits.from_yaml(cfg)
        assert limits.max_positions == 2
        assert limits.stop_loss_pct == Decimal("1.5")
        assert limits.max_total_notional_usd == Decimal("5000")
        assert limits.min_apr_pct == Decimal("15.0")

    def test_partial_override_preserves_defaults(self):
        limits = RiskLimits.from_yaml({"risk": {"max_positions": 10}})
        assert limits.max_positions == 10
        assert limits.stop_loss_pct == Decimal("2.0")


# ---------------------------------------------------------------------------
# RiskGuard.check_open — 组合级规则
# ---------------------------------------------------------------------------


class TestRiskGuardCheckOpen:
    def test_no_violations_when_all_pass(self):
        guard = _default_guard()
        assert guard.check_open([], new_notional=Decimal("500")) == []

    def test_max_positions_violated(self):
        guard = _default_guard(max_positions=2)
        positions = [_make_open_position("500"), _make_open_position("500")]
        violations = guard.check_open(positions, new_notional=Decimal("300"))
        assert any(v.rule == "max_positions" for v in violations)

    def test_max_positions_not_violated_below_limit(self):
        guard = _default_guard(max_positions=3)
        positions = [_make_open_position("500"), _make_open_position("500")]
        violations = guard.check_open(positions, new_notional=Decimal("300"))
        assert not any(v.rule == "max_positions" for v in violations)

    def test_max_position_size_violated(self):
        guard = _default_guard(max_position_size_usd=Decimal("1000"))
        violations = guard.check_open([], new_notional=Decimal("1500"))
        assert any(v.rule == "max_position_size_usd" for v in violations)

    def test_min_position_size_violated(self):
        guard = _default_guard(min_position_size_usd=Decimal("50"))
        violations = guard.check_open([], new_notional=Decimal("10"))
        assert any(v.rule == "min_position_size_usd" for v in violations)

    def test_max_total_notional_violated(self):
        guard = _default_guard(max_total_notional_usd=Decimal("1000"))
        positions = [_make_open_position("800")]
        violations = guard.check_open(positions, new_notional=Decimal("300"))
        assert any(v.rule == "max_total_notional_usd" for v in violations)

    def test_max_total_notional_exact_limit_passes(self):
        guard = _default_guard(max_total_notional_usd=Decimal("1000"))
        positions = [_make_open_position("700")]
        violations = guard.check_open(positions, new_notional=Decimal("300"))
        assert not any(v.rule == "max_total_notional_usd" for v in violations)

    def test_apr_below_min_violated(self):
        guard = _default_guard(min_apr_pct=Decimal("10"))
        violations = guard.check_open([], new_notional=Decimal("500"), apr_pct=Decimal("8"))
        assert any(v.rule == "min_apr_pct" for v in violations)

    def test_apr_above_min_passes(self):
        guard = _default_guard(min_apr_pct=Decimal("10"))
        violations = guard.check_open([], new_notional=Decimal("500"), apr_pct=Decimal("15"))
        assert not any(v.rule == "min_apr_pct" for v in violations)

    def test_apr_none_skips_check(self):
        guard = _default_guard(min_apr_pct=Decimal("10"))
        violations = guard.check_open([], new_notional=Decimal("500"), apr_pct=None)
        assert not any(v.rule == "min_apr_pct" for v in violations)

    def test_closed_positions_not_counted_toward_max(self):
        guard = _default_guard(max_positions=1)
        closed = _make_open_position("500")
        closed.mark_closed(ExitReason.MANUAL)
        violations = guard.check_open([closed], new_notional=Decimal("500"))
        assert not any(v.rule == "max_positions" for v in violations)

    def test_multiple_violations_returned(self):
        guard = _default_guard(
            max_positions=0,
            max_position_size_usd=Decimal("100"),
        )
        violations = guard.check_open([], new_notional=Decimal("500"))
        assert len(violations) >= 2

    def test_violation_carries_correct_values(self):
        guard = _default_guard(max_position_size_usd=Decimal("1000"))
        violations = guard.check_open([], new_notional=Decimal("1500"))
        v = next(v for v in violations if v.rule == "max_position_size_usd")
        assert v.current_value == Decimal("1500")
        assert v.limit_value == Decimal("1000")


# ---------------------------------------------------------------------------
# RiskGuard.check_position — 单仓级规则
# ---------------------------------------------------------------------------


class TestRiskGuardCheckPosition:
    def test_no_violations_healthy_position(self):
        guard = _default_guard()
        pos = _make_open_position("500")
        pos.funding_received = Decimal("5")
        assert guard.check_position(pos) == []

    def test_stop_loss_triggered(self):
        guard = _default_guard(stop_loss_pct=Decimal("2.0"))
        pos = _make_open_position("500")
        pos.realized_pnl = Decimal("-15")  # 亏损 3% > 2%
        violations = guard.check_position(pos)
        assert any(v.rule == "stop_loss_pct" for v in violations)

    def test_stop_loss_not_triggered_below_threshold(self):
        guard = _default_guard(stop_loss_pct=Decimal("2.0"))
        pos = _make_open_position("500")
        pos.realized_pnl = Decimal("-5")  # 亏损 1% < 2%
        assert not any(v.rule == "stop_loss_pct" for v in guard.check_position(pos))

    def test_max_hold_hours_triggered(self):
        guard = _default_guard(max_hold_hours=Decimal("8"))
        pos = _make_open_position("500")
        pos.opened_at = datetime.now(UTC) - timedelta(hours=10)
        violations = guard.check_position(pos)
        assert any(v.rule == "max_hold_hours" for v in violations)

    def test_max_hold_hours_not_triggered_within_limit(self):
        guard = _default_guard(max_hold_hours=Decimal("168"))
        pos = _make_open_position("500")
        pos.opened_at = datetime.now(UTC) - timedelta(hours=24)
        assert not any(v.rule == "max_hold_hours" for v in guard.check_position(pos))

    def test_closed_position_returns_no_violations(self):
        guard = _default_guard(stop_loss_pct=Decimal("0.0"))
        pos = _make_open_position("500")
        pos.mark_closed(ExitReason.MANUAL)
        pos.realized_pnl = Decimal("-100")
        assert guard.check_position(pos) == []


# ---------------------------------------------------------------------------
# RiskGuard.assert_can_open
# ---------------------------------------------------------------------------


class TestAssertCanOpen:
    def test_raises_risk_limit_error_on_violation(self):
        guard = _default_guard(max_positions=0)
        with pytest.raises(RiskLimitError) as exc_info:
            guard.assert_can_open([], new_notional=Decimal("500"))
        assert len(exc_info.value.violations) >= 1

    def test_does_not_raise_when_all_clear(self):
        guard = _default_guard()
        guard.assert_can_open([], new_notional=Decimal("500"))  # 不应抛出

    def test_error_message_contains_rule_name(self):
        guard = _default_guard(max_positions=0)
        with pytest.raises(RiskLimitError) as exc_info:
            guard.assert_can_open([], new_notional=Decimal("500"))
        assert "max_positions" in str(exc_info.value)


# ---------------------------------------------------------------------------
# RiskViolation 不可变性
# ---------------------------------------------------------------------------


class TestRiskViolation:
    def test_is_frozen_dataclass(self):
        v = RiskViolation(
            rule="test_rule",
            message="测试消息",
            current_value=Decimal("5"),
            limit_value=Decimal("3"),
        )
        with pytest.raises((AttributeError, TypeError)):
            v.rule = "other"  # type: ignore[misc]
