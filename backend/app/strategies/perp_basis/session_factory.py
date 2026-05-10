"""#02 perp_basis paper trading 工厂

从 yaml 配置 + 已就绪 adapter 字典 + scanner 构造完整 PerpBasisPaperSession。
统一所有策略的 session 构造模式（与 funding_rate / spot_perp_basis 对齐）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.execution.live_broker import LiveBroker
from app.strategies.perp_basis.paper_trading import PerpBasisPaperSession


def build_perp_basis_paper_session(
    cfg: dict,
    adapters: dict[str, Any],
    scanner: Any,
    fee_rate: Decimal = Decimal("0.0004"),
    perp_leverage: Decimal = Decimal("5"),
    market_data_hub: Any = None,
) -> PerpBasisPaperSession | None:
    """从 yaml dict + adapters + scanner 构造 perp_basis paper session。

    仅鉴权 adapter（有 _api_key）参与 broker 字典。无可用 broker → 返回 None。
    """
    entry = cfg.get("entry", {}) or {}
    pos_cfg = cfg.get("position", {}) or {}
    exit_cfg = cfg.get("exit", {}) or {}
    scan_cfg = cfg.get("scanning", {}) or {}

    brokers = {
        n: LiveBroker(adapter=a, fee_rate=fee_rate, perp_leverage=perp_leverage)
        for n, a in adapters.items()
        if getattr(a, "_api_key", "")
    }
    if not brokers:
        return None

    risk = cfg.get("risk", {}) or {}
    return PerpBasisPaperSession(
        scanner=scanner,
        brokers=brokers,
        notional_per_position=Decimal(str(pos_cfg.get("notional_per_position", 50))),
        max_concurrent=int(pos_cfg.get("max_concurrent", 2)),
        min_diff_apr_pct=Decimal(str(entry.get("min_diff_apr_pct", "50.0"))),
        max_hold_hours=Decimal(str(exit_cfg.get("max_hold_hours", 48))),
        min_hold_hours=Decimal(str(exit_cfg.get("min_hold_hours", 4))),
        exit_diff_apr_pct=Decimal(str(exit_cfg.get("exit_diff_apr_pct", 5))),
        stop_price_divergence_pct=Decimal(str(
            risk.get("stop_price_divergence_pct", "5.0")
        )),
        scan_interval_seconds=float(scan_cfg.get("scan_interval_seconds", 60)),
        market_data_hub=market_data_hub,
    )
