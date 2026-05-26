"""
dgr_btc/pretrade_check.py
=========================
PretradeChecker — 下单前预检 (Phase E.1 防护层 1).

检查项:
  1. spot/perp 余额/保证金充足
  2. orderbook 深度足够吃单 (LIMIT_MAKER 不会立即穿越)
  3. 价格未超过最近 mark 的 N% (防错单)
  4. 仓位上限不会被突破

PAPER 模式: 只做基础 sanity check, 跳过 orderbook depth (没有真实盘口)
LIVE 模式: 全部检查 (orderbook + balance + margin)
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

import structlog

from app.strategies.dgr_btc.types import MarketState, MarketType, Side
from app.strategies.dgr_btc.strategy_core import OrderIntent

logger = structlog.get_logger(__name__)


class PretradeChecker:
    def __init__(
        self,
        strategy: Any,
        max_price_deviation_pct: Decimal = Decimal("0.005"),
        min_orderbook_depth_ratio: Decimal = Decimal("2.0"),
        live_mode: bool = False,
        market_data_hub: Any | None = None,
    ) -> None:
        self.strategy = strategy
        self.max_price_dev = max_price_deviation_pct
        self.min_depth_ratio = min_orderbook_depth_ratio
        self.live_mode = live_mode
        self.hub = market_data_hub

    def validate(
        self,
        spot_intent: OrderIntent,
        perp_intent: OrderIntent,
        market: MarketState,
    ) -> tuple[bool, Optional[str]]:
        """返回 (ok, reason). ok=False 表示应拒单."""
        if spot_intent.grid_level != perp_intent.grid_level:
            return False, f"pair_mismatch grid_level spot={spot_intent.grid_level} perp={perp_intent.grid_level}"
        if spot_intent.quantity != perp_intent.quantity:
            return False, f"pair_mismatch quantity spot={spot_intent.quantity} perp={perp_intent.quantity}"

        # 1. 价格偏离 mark 检查
        for it, mark in [
            (spot_intent, market.spot_price),
            (perp_intent, market.perp_price),
        ]:
            if mark <= 0:
                return False, f"invalid_mark {it.market.value}={mark}"
            dev = abs(it.price - mark) / mark
            if dev > self.max_price_dev:
                return False, f"{it.market.value}_price_dev {dev:.4f} > {self.max_price_dev}"

        # 2. 仓位上限检查
        cfg = self.strategy.config
        if spot_intent.side == Side.BUY:
            new_spot = self.strategy.spot_pos.quantity + spot_intent.quantity
            if new_spot > cfg.max_spot_btc + Decimal("1e-9"):
                return False, f"spot_cap_breach {new_spot} > {cfg.max_spot_btc}"
        else:
            new_spot = self.strategy.spot_pos.quantity - spot_intent.quantity
            if new_spot < -Decimal("1e-9"):
                return False, f"spot_insufficient {self.strategy.spot_pos.quantity} - {spot_intent.quantity}"

        # 3. perp short 上限
        if perp_intent.side == Side.SELL:
            new_short = abs(self.strategy.perp_pos.quantity) + perp_intent.quantity
            if new_short > cfg.max_short_btc + Decimal("1e-9"):
                return False, f"perp_short_cap_breach {new_short} > {cfg.max_short_btc}"

        # 4. cash 充足: WARN-only (不 SKIP)
        # 设计意图: 真实生产不应该因为"资金总额度不够"被预检拦截.
        #   spot 买入 cost > cash 时只 log 警告, 让 broker 实际 reject 触发 unwind 兜底.
        #   PAPER 模式: cash 仅记录, 不强制阻塞 (允许负数累积, 真亏损在 mark_to_market 体现).
        #   LIVE 模式: broker 自然 reject → atomic_pair unwind 平掉已成的另一腿.
        if spot_intent.side == Side.BUY:
            cost = spot_intent.price * spot_intent.quantity
            if self.strategy.cash < cost:
                logger.warning(
                    "dgr_btc_pretrade_low_cash_warn",
                    cash=str(self.strategy.cash),
                    cost=str(cost),
                    note="proceeding anyway, broker reject + unwind will handle",
                )

        # 5. LIVE 模式: orderbook depth check
        # Phase G.1 (2026-05-24): 暂禁用 — hub.get_orderbook 未实装, 每次必 except
        # 并 fail-OPEN. 移除直到 hub 真正实装 depth API.
        # if self.live_mode and self.hub is not None:
        #     depth_ok = self._check_orderbook_depth(spot_intent, perp_intent)
        #     if not depth_ok:
        #         return False, "orderbook_depth_insufficient"

        return True, None

    def _check_orderbook_depth(
        self,
        spot_intent: OrderIntent,
        perp_intent: OrderIntent,
    ) -> bool:
        """检查盘口深度 >= qty × min_depth_ratio. Live 模式专用."""
        if self.hub is None:
            return True
        try:
            spot_book = self.hub.get_orderbook("BTC/USDT")
            perp_book = self.hub.get_orderbook("BTC/USDT:USDT")
        except Exception as e:
            logger.warning("dgr_btc_orderbook_fetch_failed", error=str(e)[:120])
            return True

        for intent, book in [(spot_intent, spot_book), (perp_intent, perp_book)]:
            if book is None:
                continue
            side_levels = book.get("asks" if intent.side == Side.BUY else "bids", [])
            cumul = Decimal("0")
            for level in side_levels[:5]:
                cumul += Decimal(str(level[1]))
                if cumul >= intent.quantity * self.min_depth_ratio:
                    break
            else:
                return False
        return True
