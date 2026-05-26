"""
app.strategies.dgr_btc — 单边 Martingale + Recenter + Stop Loss (#13 v2)

Revamp 2026-05-26: from paired_inverse spot+perp hedge → single-side martingale.

入口:
  - MartingaleEngine / EngineConfig / StrategyState / Layer / Decision / DecisionKind
    （新核心：pure logic，回测与 LIVE 共享）
  - DgrBtcStrategyConfig: dataclass + from_yaml / apply_overrides
  - GridManager / GridTrigger: 单向网格（layers list）
  - RiskFilter / RiskLevel / RiskReport / RiskTrigger: 风控
  - Position / Trade / MarketState / PortfolioSnapshot: 策略内部类型

旧模块（已删除，本注释作历史标记）:
  - atomic_pair, delta_hedger, maker_reprice（paired_inverse 专属，单边不需要）
"""
# NEW core (Martingale + Recenter + SL) — pure logic
from app.strategies.dgr_btc.engine import (
    Decision,
    DecisionKind,
    EngineConfig,
    Layer,
    MartingaleEngine,
    StrategyState,
)
from app.strategies.dgr_btc.types import (
    GridLevel,
    MarketState,
    MarketType,
    Order,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    Position,
    Side,
    Trade,
)

# Legacy modules (待 P3 后续 phase 改造为调用 engine)
# 注：不在此 __init__ import 它们，避免目前 delta_hedger 已删除的破坏性 ImportError。
# Caller 需直接 from app.strategies.dgr_btc.<module> import ...
# 改造完成后会重新加回这里。

__all__ = [
    # Engine (new core)
    "MartingaleEngine",
    "EngineConfig",
    "StrategyState",
    "Layer",
    "Decision",
    "DecisionKind",
    # Types (保留通用)
    "Position",
    "Trade",
    "MarketState",
    "PortfolioSnapshot",
    "Order",
    "OrderStatus",
    "OrderType",
    "Side",
    "MarketType",
    "GridLevel",
]
