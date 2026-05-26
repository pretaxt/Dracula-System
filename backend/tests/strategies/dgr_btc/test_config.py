"""Tests for dgr_btc.config — yaml round-trip + immutability + spec defaults."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.strategies.dgr_btc.config import DgrBtcStrategyConfig


def test_defaults_match_spec():
    """⭐ 核心: 默认值必须对齐文档 §2 + Codex 真实 yaml。

    防止 v2 漏 trend_grids_threshold=5 的错误复发。
    """
    cfg = DgrBtcStrategyConfig()
    # dynamic bounds
    assert cfg.width_pct == Decimal("0.15")
    assert cfg.dynamic_bounds_enabled is True
    # recenter
    assert cfg.recenter_enabled is True
    assert cfg.recenter_trigger_pct == Decimal("0.12")
    assert cfg.recenter_cooldown_sec == 600
    # trend filter ⭐
    assert cfg.risk_trend_grids_threshold == 5
    # risk multi
    assert cfg.risk_hourly_vol_threshold == Decimal("1.5")
    assert cfg.risk_margin_ratio_min == Decimal("0.35")
    assert cfg.risk_max_daily_loss_pct == Decimal("0.05")
    assert cfg.risk_max_drawdown_pct == Decimal("0.15")
    # paper sized (1/10 of doc)
    assert cfg.total_capital_usdt == Decimal("10000")
    assert cfg.grid_qty_per_grid == Decimal("0.002")
    assert cfg.max_spot_btc == Decimal("0.1")


def test_from_yaml_round_trip():
    yaml_data = {
        "enabled": True,
        "instance_name": "测试实例",
        "capital": {"total_usdt": 50000},
        "grid": {
            "step_usdt": 500,
            "qty_per_grid": 0.05,
            "dynamic_bounds": {
                "width_pct": 0.18,
                "recenter_trigger_pct": 0.10,
                "recenter_cooldown_sec": 300,
            },
        },
        "risk": {"trend_grids_threshold": 7},
        "position_limits": {"max_spot_btc": 0.5},
    }
    cfg = DgrBtcStrategyConfig.from_yaml(yaml_data)
    assert cfg.enabled is True
    assert cfg.instance_name == "测试实例"
    assert cfg.total_capital_usdt == Decimal("50000")
    assert cfg.grid_step_usdt == Decimal("500")
    assert cfg.grid_qty_per_grid == Decimal("0.05")
    assert cfg.width_pct == Decimal("0.18")
    assert cfg.recenter_trigger_pct == Decimal("0.10")
    assert cfg.recenter_cooldown_sec == 300
    assert cfg.risk_trend_grids_threshold == 7
    assert cfg.max_spot_btc == Decimal("0.5")


def test_apply_overrides_immutable():
    cfg = DgrBtcStrategyConfig()
    new_cfg = cfg.apply_overrides({"capital": {"total_usdt": 99999}})
    # 旧对象不变
    assert cfg.total_capital_usdt == Decimal("10000")
    # 新对象更新
    assert new_cfg.total_capital_usdt == Decimal("99999")
    # 其他字段保留
    assert new_cfg.width_pct == cfg.width_pct
    assert new_cfg.risk_trend_grids_threshold == cfg.risk_trend_grids_threshold


def test_apply_overrides_nested_dynamic_bounds():
    cfg = DgrBtcStrategyConfig()
    new_cfg = cfg.apply_overrides({
        "grid": {
            "dynamic_bounds": {
                "recenter_trigger_pct": 0.08,
                "recenter_cooldown_sec": 1200,
            }
        }
    })
    assert new_cfg.recenter_trigger_pct == Decimal("0.08")
    assert new_cfg.recenter_cooldown_sec == 1200
    # 未提及字段保留默认
    assert new_cfg.width_pct == Decimal("0.15")
    assert new_cfg.recenter_enabled is True


def test_from_yaml_empty_returns_defaults():
    cfg = DgrBtcStrategyConfig.from_yaml(None)
    assert cfg.width_pct == Decimal("0.15")
    cfg2 = DgrBtcStrategyConfig.from_yaml({})
    assert cfg2.risk_trend_grids_threshold == 5
