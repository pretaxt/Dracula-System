"""
app.strategies.dgr_btc — 动态网格 + 再定心策略 (#13)

paired_inverse + dynamic bounds + price-deviation recenter + trend filter halt
+ RiskEngine 多重风控。

完全独立实现, 不复用其他策略（#11/#12 等）代码。

入口:
  - DgrBtcStrategyConfig: dataclass + from_yaml / apply_overrides
  - DgrBtcStrategy: 主控制器 (on_tick / on_trade / apply_funding / maybe_recenter / get_snapshot)
  - GridManager / GridTrigger: 动态网格 + recenter rebuild
  - DeltaHedger / DeltaState / HedgeAction: Delta 约束
  - RiskFilter / RiskLevel / RiskReport / RiskTrigger: 7 维风控
  - Position / Trade / MarketState / PortfolioSnapshot: 策略内部类型
"""
from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.delta_hedger import (
    DeltaHedger,
    DeltaState,
    HedgeAction,
)
from app.strategies.dgr_btc.grid_manager import (
    GridManager,
    GridTrigger,
)
from app.strategies.dgr_btc.risk_filter import (
    RiskFilter,
    RiskLevel,
    RiskReport,
    RiskTrigger,
)
from app.strategies.dgr_btc.strategy_core import (
    DgrBtcStrategy,
    OrderIntent,
    RecenterEvent,
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


__all__ = [
    "DgrBtcStrategyConfig",
    "DgrBtcStrategy",
    "OrderIntent",
    "RecenterEvent",
    "GridManager",
    "GridTrigger",
    "DeltaHedger",
    "DeltaState",
    "HedgeAction",
    "RiskFilter",
    "RiskLevel",
    "RiskReport",
    "RiskTrigger",
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
