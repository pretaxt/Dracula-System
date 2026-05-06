"""CEX 适配器包

当前实现:
  BinanceAdapter — Binance 现货 + USDM 永续 (Week 3)

后续实现 (Week 4+):
  BybitAdapter, OKXAdapter, HTXAdapter, BitgetAdapter
"""
from app.exchanges.cex.binance import BinanceAdapter

__all__ = ["BinanceAdapter"]
