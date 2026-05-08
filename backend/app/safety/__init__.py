"""Safety net modules — runtime guards beyond strategy logic.

- liquidation_watcher: Binance USDM 用户数据流 WS 监听（毫秒级实时）
- polling_liquidation_watcher: OKX 等无 WS 的交易所，每 30s 轮询 fetch_positions
"""
from app.safety.liquidation_watcher import LiquidationWatcher
from app.safety.polling_liquidation_watcher import PollingLiquidationWatcher

__all__ = ["LiquidationWatcher", "PollingLiquidationWatcher"]
