"""Safety net modules — runtime guards beyond strategy logic.

Currently:
  - liquidation_watcher: Binance USDM user-data-stream listener that detects
    forced perp liquidations and closes the orphaned spot leg.
"""
from app.safety.liquidation_watcher import LiquidationWatcher

__all__ = ["LiquidationWatcher"]
