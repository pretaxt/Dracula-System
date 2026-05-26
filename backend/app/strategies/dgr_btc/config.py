"""
dgr_btc/config.py
=================
DgrBtcStrategyConfig dataclass — yaml + runtime override 适配。

vs HedgedGridStrategyConfig 关键增量:
  ✅ dynamic_bounds 段 (enabled / width_pct / min_width_pct / max_width_pct)
  ✅ recenter 段 (enabled / trigger_pct / cooldown_sec)
  ✅ 默认值反映 Codex 真实生产配置 (width_pct=0.15, recenter_trigger_pct=0.12,
     trend_grids_threshold=5 - 这是 v2 漏掉的)

不可变模式: apply_overrides() 返回新副本。
不 import hedged_grid 任何代码（完全独立）。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal


_ZERO = Decimal("0")


@dataclass
class DgrBtcStrategyConfig:
    """动态网格 + 再定心策略完整运行参数。"""

    # ---------- 启停 ----------
    enabled: bool = False
    instance_name: str = "动态网格再定心"

    # ---------- 交易标的 ----------
    symbol_spot: str = "BTC/USDT"
    symbol_perp: str = "BTC/USDT:USDT"
    symbol_base: str = "BTC"
    symbol_quote: str = "USDT"
    exchange: str = "binance"

    # ---------- 资金（paper 起步 $10k = doc $100k / 10）----------
    total_capital_usdt: Decimal = Decimal("10000")
    spot_initial_btc: Decimal = Decimal("0.05")
    short_initial_btc: Decimal = Decimal("0.05")
    reserve_ratio: Decimal = Decimal("0.2")

    # ---------- 网格 ----------
    grid_center_price: Decimal = _ZERO  # 0 = 启动时取实时价
    grid_step_usdt: Decimal = Decimal("250")
    grid_qty_per_grid: Decimal = Decimal("0.002")

    # ---------- Dynamic bounds (doc §2 + §3) ----------
    dynamic_bounds_enabled: bool = True
    width_pct: Decimal = Decimal("0.15")  # ±15%
    min_width_pct: Decimal = Decimal("0.15")
    max_width_pct: Decimal = Decimal("0.20")

    # ---------- Recenter (doc §9 + §10) ----------
    recenter_enabled: bool = True
    recenter_trigger_pct: Decimal = Decimal("0.12")  # |price-center|/center >= 12%
    recenter_cooldown_sec: int = 600  # 10min

    # ---------- Delta 对冲 (doc §8) ----------
    delta_target: Decimal = _ZERO
    delta_upper_limit: Decimal = Decimal("0.02")  # paper 缩小: 0.2 × 0.1
    delta_lower_limit: Decimal = Decimal("-0.02")
    delta_rebalance_threshold: Decimal = Decimal("0.05")

    # ---------- 仓位上限（paper 缩小到 0.1 BTC = doc 1.0 / 10）----------
    max_spot_btc: Decimal = Decimal("0.1")
    max_short_btc: Decimal = Decimal("0.1")
    leverage: int = 10

    # ---------- 风控（Codex risk yaml，⭐ trend_grids_threshold=5）----------
    risk_trend_grids_threshold: int = 5  # ⭐ 核心 alpha 保护 (v2 漏掉)
    risk_trend_density_factor: Decimal = Decimal("2.0")
    risk_hourly_vol_threshold: Decimal = Decimal("1.5")
    risk_margin_ratio_min: Decimal = Decimal("0.35")
    risk_liquidation_buffer: Decimal = Decimal("0.20")
    risk_funding_filter_enabled: bool = True
    risk_funding_threshold: Decimal = _ZERO
    risk_min_orderbook_depth_usdt: Decimal = Decimal("50000")
    risk_max_daily_loss_pct: Decimal = Decimal("0.05")
    risk_max_drawdown_pct: Decimal = Decimal("0.15")

    # ---------- 执行 ----------
    exec_order_type: str = "LIMIT_MAKER"
    exec_post_only: bool = True
    exec_retry_on_fail: int = 3
    exec_retry_delay_ms: int = 500
    exec_slippage_tolerance_bps: int = 5

    # ---------- Live safety ----------
    live_mode: bool = False
    live_safety_enabled: bool = False
    live_safety_dry_run: bool = False
    live_safety_telegram_alerts_enabled: bool = True
    live_safety_max_order_usd: Decimal = Decimal("50")  # paper: $50 单笔上限
    live_safety_max_daily_order_count: int = 200
    live_safety_max_daily_notional_usd: Decimal = Decimal("1000")
    live_safety_max_price_deviation_pct: Decimal = Decimal("0.5")
    live_safety_audit_log_path: str = "/app/state/dgr_btc_audit.jsonl"

    # ---------- 回测专用 ----------
    backtest_spot_fee: Decimal = Decimal("0.001")
    backtest_perp_maker_fee: Decimal = Decimal("0.0002")
    backtest_perp_taker_fee: Decimal = Decimal("0.0005")
    backtest_slippage_bps: int = 1
    backtest_maker_ratio: Decimal = Decimal("0.7")
    backtest_funding_mode: str = "historical"
    backtest_funding_constant: Decimal = Decimal("0.0001")
    backtest_funding_interval_hours: int = 8

    # ------------------------------------------------------------------
    # yaml 加载
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, yaml_data: dict | None) -> "DgrBtcStrategyConfig":
        """从 yaml dict 构造（dgr_btc_main.yaml schema）。"""
        if not yaml_data:
            return cls()
        d = yaml_data

        def _D(v, default):
            try:
                return Decimal(str(v)) if v is not None else default
            except Exception:
                return default

        def _get(path: list[str], default=None):
            cur = d
            for k in path:
                if not isinstance(cur, dict) or k not in cur:
                    return default
                cur = cur[k]
            return cur if cur is not None else default

        return cls(
            enabled=bool(_get(["enabled"], False)),
            instance_name=str(_get(["instance_name"], "动态网格再定心")),
            # symbol
            symbol_spot=str(_get(["symbol", "spot"], "BTC/USDT")),
            symbol_perp=str(_get(["symbol", "perp"], "BTC/USDT:USDT")),
            symbol_base=str(_get(["symbol", "base"], "BTC")),
            symbol_quote=str(_get(["symbol", "quote"], "USDT")),
            exchange=str(_get(["symbol", "exchange"], "binance")),
            # capital
            total_capital_usdt=_D(_get(["capital", "total_usdt"]), Decimal("10000")),
            spot_initial_btc=_D(_get(["capital", "spot_initial_btc"]), Decimal("0.05")),
            short_initial_btc=_D(_get(["capital", "short_initial_btc"]), Decimal("0.05")),
            reserve_ratio=_D(_get(["capital", "reserve_ratio"]), Decimal("0.2")),
            # grid
            grid_center_price=_D(_get(["grid", "center_price"]), _ZERO),
            grid_step_usdt=_D(_get(["grid", "step_usdt"]), Decimal("250")),
            grid_qty_per_grid=_D(_get(["grid", "qty_per_grid"]), Decimal("0.002")),
            # dynamic_bounds (新)
            dynamic_bounds_enabled=bool(
                _get(["grid", "dynamic_bounds", "enabled"], True)
            ),
            width_pct=_D(_get(["grid", "dynamic_bounds", "width_pct"]), Decimal("0.15")),
            min_width_pct=_D(
                _get(["grid", "dynamic_bounds", "min_width_pct"]), Decimal("0.15")
            ),
            max_width_pct=_D(
                _get(["grid", "dynamic_bounds", "max_width_pct"]), Decimal("0.20")
            ),
            # recenter (新)
            recenter_enabled=bool(
                _get(["grid", "dynamic_bounds", "recenter_enabled"], True)
            ),
            recenter_trigger_pct=_D(
                _get(["grid", "dynamic_bounds", "recenter_trigger_pct"]),
                Decimal("0.12"),
            ),
            recenter_cooldown_sec=int(
                _get(["grid", "dynamic_bounds", "recenter_cooldown_sec"], 600)
            ),
            # delta
            delta_target=_D(_get(["delta", "target"]), _ZERO),
            delta_upper_limit=_D(_get(["delta", "upper_limit"]), Decimal("0.02")),
            delta_lower_limit=_D(_get(["delta", "lower_limit"]), Decimal("-0.02")),
            delta_rebalance_threshold=_D(
                _get(["delta", "rebalance_threshold"]), Decimal("0.05")
            ),
            # position
            max_spot_btc=_D(_get(["position_limits", "max_spot_btc"]), Decimal("0.1")),
            max_short_btc=_D(_get(["position_limits", "max_short_btc"]), Decimal("0.1")),
            leverage=int(_get(["position_limits", "leverage"], 10)),
            # risk
            risk_trend_grids_threshold=int(
                _get(["risk", "trend_grids_threshold"], 5)
            ),
            risk_trend_density_factor=_D(
                _get(["risk", "trend_density_factor"]), Decimal("2.0")
            ),
            risk_hourly_vol_threshold=_D(
                _get(["risk", "hourly_vol_threshold"]), Decimal("1.5")
            ),
            risk_margin_ratio_min=_D(
                _get(["risk", "margin_ratio_min"]), Decimal("0.35")
            ),
            risk_liquidation_buffer=_D(
                _get(["risk", "liquidation_buffer"]), Decimal("0.20")
            ),
            risk_funding_filter_enabled=bool(
                _get(["risk", "funding_filter_enabled"], True)
            ),
            risk_funding_threshold=_D(_get(["risk", "funding_threshold"]), _ZERO),
            risk_min_orderbook_depth_usdt=_D(
                _get(["risk", "min_orderbook_depth_usdt"]), Decimal("50000")
            ),
            risk_max_daily_loss_pct=_D(
                _get(["risk", "max_daily_loss_pct"]), Decimal("0.05")
            ),
            risk_max_drawdown_pct=_D(
                _get(["risk", "max_drawdown_pct"]), Decimal("0.15")
            ),
            # execution
            exec_order_type=str(_get(["execution", "order_type"], "LIMIT_MAKER")),
            exec_post_only=bool(_get(["execution", "post_only"], True)),
            exec_retry_on_fail=int(_get(["execution", "retry_on_fail"], 3)),
            exec_retry_delay_ms=int(_get(["execution", "retry_delay_ms"], 500)),
            exec_slippage_tolerance_bps=int(
                _get(["execution", "slippage_tolerance_bps"], 5)
            ),
            # live_safety
            live_mode=bool(_get(["live_mode"], False)),
            live_safety_enabled=bool(_get(["live_safety", "enabled"], False)),
            live_safety_dry_run=bool(_get(["live_safety", "dry_run"], False)),
            live_safety_telegram_alerts_enabled=bool(
                _get(["live_safety", "telegram_alerts_enabled"], True)
            ),
            live_safety_max_order_usd=_D(
                _get(["live_safety", "max_order_usd"]), Decimal("50")
            ),
            live_safety_max_daily_order_count=int(
                _get(["live_safety", "max_daily_order_count"], 200)
            ),
            live_safety_max_daily_notional_usd=_D(
                _get(["live_safety", "max_daily_notional_usd"]), Decimal("1000")
            ),
            live_safety_max_price_deviation_pct=_D(
                _get(["live_safety", "max_price_deviation_pct"]), Decimal("0.5")
            ),
            live_safety_audit_log_path=str(
                _get(
                    ["live_safety", "audit_log_path"],
                    "/app/state/dgr_btc_audit.jsonl",
                )
            ),
            # backtest
            backtest_spot_fee=_D(_get(["backtest", "spot_fee"]), Decimal("0.001")),
            backtest_perp_maker_fee=_D(
                _get(["backtest", "perp_maker_fee"]), Decimal("0.0002")
            ),
            backtest_perp_taker_fee=_D(
                _get(["backtest", "perp_taker_fee"]), Decimal("0.0005")
            ),
            backtest_slippage_bps=int(_get(["backtest", "slippage_bps"], 1)),
            backtest_maker_ratio=_D(
                _get(["backtest", "maker_ratio"]), Decimal("0.7")
            ),
            backtest_funding_mode=str(
                _get(["backtest", "funding_mode"], "historical")
            ),
            backtest_funding_constant=_D(
                _get(["backtest", "funding_constant"]), Decimal("0.0001")
            ),
            backtest_funding_interval_hours=int(
                _get(["backtest", "funding_interval_hours"], 8)
            ),
        )

    def apply_overrides(self, overrides: dict | None) -> "DgrBtcStrategyConfig":
        """从 runtime override dict 生成新副本（不可变模式）。

        支持白名单字段任意子集 — 所有 yaml 字段均可热改。
        """
        if not overrides:
            return self

        def _D(v, default):
            try:
                return Decimal(str(v)) if v is not None else default
            except Exception:
                return default

        kwargs: dict = {}
        if "enabled" in overrides:
            kwargs["enabled"] = bool(overrides["enabled"])
        if "instance_name" in overrides:
            kwargs["instance_name"] = str(overrides["instance_name"])

        # symbol
        sym = overrides.get("symbol", {})
        if isinstance(sym, dict):
            for k_yaml, k_cls in (
                ("spot", "symbol_spot"),
                ("perp", "symbol_perp"),
                ("base", "symbol_base"),
                ("quote", "symbol_quote"),
                ("exchange", "exchange"),
            ):
                if k_yaml in sym:
                    kwargs[k_cls] = str(sym[k_yaml])

        # capital
        cap = overrides.get("capital", {})
        if isinstance(cap, dict):
            mapping = {
                "total_usdt": "total_capital_usdt",
                "spot_initial_btc": "spot_initial_btc",
                "short_initial_btc": "short_initial_btc",
                "reserve_ratio": "reserve_ratio",
            }
            for k_yaml, k_cls in mapping.items():
                if k_yaml in cap:
                    kwargs[k_cls] = _D(cap[k_yaml], getattr(self, k_cls))

        # grid 主段
        grid = overrides.get("grid", {})
        if isinstance(grid, dict):
            mapping = {
                "center_price": "grid_center_price",
                "step_usdt": "grid_step_usdt",
                "qty_per_grid": "grid_qty_per_grid",
            }
            for k_yaml, k_cls in mapping.items():
                if k_yaml in grid:
                    kwargs[k_cls] = _D(grid[k_yaml], getattr(self, k_cls))
            # dynamic_bounds 子段
            db = grid.get("dynamic_bounds", {})
            if isinstance(db, dict):
                if "enabled" in db:
                    kwargs["dynamic_bounds_enabled"] = bool(db["enabled"])
                if "width_pct" in db:
                    kwargs["width_pct"] = _D(db["width_pct"], self.width_pct)
                if "min_width_pct" in db:
                    kwargs["min_width_pct"] = _D(
                        db["min_width_pct"], self.min_width_pct
                    )
                if "max_width_pct" in db:
                    kwargs["max_width_pct"] = _D(
                        db["max_width_pct"], self.max_width_pct
                    )
                if "recenter_enabled" in db:
                    kwargs["recenter_enabled"] = bool(db["recenter_enabled"])
                if "recenter_trigger_pct" in db:
                    kwargs["recenter_trigger_pct"] = _D(
                        db["recenter_trigger_pct"], self.recenter_trigger_pct
                    )
                if "recenter_cooldown_sec" in db:
                    kwargs["recenter_cooldown_sec"] = int(db["recenter_cooldown_sec"])

        # delta
        d = overrides.get("delta", {})
        if isinstance(d, dict):
            for k_yaml, k_cls in (
                ("target", "delta_target"),
                ("upper_limit", "delta_upper_limit"),
                ("lower_limit", "delta_lower_limit"),
                ("rebalance_threshold", "delta_rebalance_threshold"),
            ):
                if k_yaml in d:
                    kwargs[k_cls] = _D(d[k_yaml], getattr(self, k_cls))

        # position_limits
        pl = overrides.get("position_limits", {})
        if isinstance(pl, dict):
            if "max_spot_btc" in pl:
                kwargs["max_spot_btc"] = _D(pl["max_spot_btc"], self.max_spot_btc)
            if "max_short_btc" in pl:
                kwargs["max_short_btc"] = _D(pl["max_short_btc"], self.max_short_btc)
            if "leverage" in pl:
                kwargs["leverage"] = int(pl["leverage"])

        # risk
        r = overrides.get("risk", {})
        if isinstance(r, dict):
            int_keys = {"trend_grids_threshold": "risk_trend_grids_threshold"}
            dec_keys = {
                "trend_density_factor": "risk_trend_density_factor",
                "hourly_vol_threshold": "risk_hourly_vol_threshold",
                "margin_ratio_min": "risk_margin_ratio_min",
                "liquidation_buffer": "risk_liquidation_buffer",
                "funding_threshold": "risk_funding_threshold",
                "min_orderbook_depth_usdt": "risk_min_orderbook_depth_usdt",
                "max_daily_loss_pct": "risk_max_daily_loss_pct",
                "max_drawdown_pct": "risk_max_drawdown_pct",
            }
            bool_keys = {"funding_filter_enabled": "risk_funding_filter_enabled"}
            for k_yaml, k_cls in int_keys.items():
                if k_yaml in r:
                    kwargs[k_cls] = int(r[k_yaml])
            for k_yaml, k_cls in dec_keys.items():
                if k_yaml in r:
                    kwargs[k_cls] = _D(r[k_yaml], getattr(self, k_cls))
            for k_yaml, k_cls in bool_keys.items():
                if k_yaml in r:
                    kwargs[k_cls] = bool(r[k_yaml])

        # execution
        e = overrides.get("execution", {})
        if isinstance(e, dict):
            if "order_type" in e:
                kwargs["exec_order_type"] = str(e["order_type"])
            if "post_only" in e:
                kwargs["exec_post_only"] = bool(e["post_only"])
            if "retry_on_fail" in e:
                kwargs["exec_retry_on_fail"] = int(e["retry_on_fail"])
            if "retry_delay_ms" in e:
                kwargs["exec_retry_delay_ms"] = int(e["retry_delay_ms"])
            if "slippage_tolerance_bps" in e:
                kwargs["exec_slippage_tolerance_bps"] = int(e["slippage_tolerance_bps"])

        # live_safety
        if "live_mode" in overrides:
            kwargs["live_mode"] = bool(overrides["live_mode"])
        ls = overrides.get("live_safety", {})
        if isinstance(ls, dict):
            if "enabled" in ls:
                kwargs["live_safety_enabled"] = bool(ls["enabled"])
            if "dry_run" in ls:
                kwargs["live_safety_dry_run"] = bool(ls["dry_run"])
            if "telegram_alerts_enabled" in ls:
                kwargs["live_safety_telegram_alerts_enabled"] = bool(
                    ls["telegram_alerts_enabled"]
                )
            for k_yaml, k_cls in (
                ("max_order_usd", "live_safety_max_order_usd"),
                ("max_daily_notional_usd", "live_safety_max_daily_notional_usd"),
                ("max_price_deviation_pct", "live_safety_max_price_deviation_pct"),
            ):
                if k_yaml in ls:
                    kwargs[k_cls] = _D(ls[k_yaml], getattr(self, k_cls))
            if "max_daily_order_count" in ls:
                kwargs["live_safety_max_daily_order_count"] = int(
                    ls["max_daily_order_count"]
                )

        return replace(self, **kwargs)
