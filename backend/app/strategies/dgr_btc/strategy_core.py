"""
dgr_btc/strategy_core.py
========================
paired_inverse 策略主控 — DgrBtcStrategy (#13)。

vs HedgedGridStrategy 关键增量:
  ✅ maybe_recenter(ts, price) - doc §9 + §10 实现
     * 检查 |price - center|/center >= recenter_trigger_pct (0.12)
     * cooldown >= recenter_cooldown_sec (600s)
     * 触发 → grid.rebuild_around(new_center, width_pct)
     * 计数 + 时间戳记录
  ✅ on_tick 在 grid.update_price 前调 maybe_recenter
  ✅ 自动从启动价格 build grid (dynamic_bounds_enabled=True)

paired_inverse 行为契约（不变, doc §5 §6 §8）:
  - 价格上穿: spot SELL + 加空 → Delta 下降
  - 价格下穿: spot BUY + 平空 → Delta 上升
  - Delta ±limit 双向硬刹车: 触限整对跳过
  - DEFENSIVE 模式: 跳加空 + 跳 buy spot (仅保留平空)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.delta_hedger import DeltaHedger
from app.strategies.dgr_btc.grid_manager import GridManager, GridTrigger
from app.strategies.dgr_btc.risk_filter import (
    RiskFilter,
    RiskLevel,
    RiskReport,
)
from app.strategies.dgr_btc.types import (
    MarketState,
    MarketType,
    PortfolioSnapshot,
    Position,
    Side,
    Trade,
)


logger = logging.getLogger(__name__)
_ZERO = Decimal("0")


@dataclass
class OrderIntent:
    """订单意图（策略生成, broker_adapter 下单层执行）。"""

    market: MarketType
    side: Side
    price: Decimal
    quantity: Decimal
    grid_level: Optional[Decimal] = None
    reason: str = ""


@dataclass
class RecenterEvent:
    """记录一次 recenter 用于审计 / Telegram alert。"""

    timestamp: datetime
    old_center: Decimal
    new_center: Decimal
    deviation_pct: Decimal


class DgrBtcStrategy:
    """动态网格 + 再定心策略主控制器 (#13).

    使用模式（mirror Codex DraculaStrategy）::

        strategy = DgrBtcStrategy(config, start_price=current_btc_price)
        for tick in market_feed:
            intents = strategy.on_tick(tick)
            for intent in intents:
                broker.submit(intent)
            strategy.on_trade(trade)  # 成交回调
    """

    def __init__(
        self,
        config: DgrBtcStrategyConfig,
        start_price: Optional[Decimal] = None,
    ):
        self.config = config

        # 决定启动 center
        if config.grid_center_price > 0:
            center = config.grid_center_price
        elif start_price is not None and start_price > 0:
            center = start_price
        else:
            raise ValueError(
                "dgr_btc: must provide start_price when grid_center_price=0"
            )

        # Grid 按 dynamic_bounds 构造
        self.grid = GridManager.from_center(
            center=center,
            width_pct=config.width_pct,
            step_usdt=config.grid_step_usdt,
            qty_per_grid=config.grid_qty_per_grid,
        )

        # Hedger
        self.hedger = DeltaHedger(
            upper_limit=config.delta_upper_limit,
            lower_limit=config.delta_lower_limit,
            max_spot=config.max_spot_btc,
            max_short=config.max_short_btc,
            target=config.delta_target,
            rebalance_threshold=config.delta_rebalance_threshold,
        )

        # Risk filter
        self.risk = RiskFilter(
            trend_grids_threshold=config.risk_trend_grids_threshold,
            hourly_vol_threshold=config.risk_hourly_vol_threshold,
            margin_ratio_min=config.risk_margin_ratio_min,
            funding_filter_enabled=config.risk_funding_filter_enabled,
            funding_threshold=config.risk_funding_threshold,
            min_orderbook_depth=config.risk_min_orderbook_depth_usdt,
            max_daily_loss_pct=config.risk_max_daily_loss_pct,
            max_drawdown_pct=config.risk_max_drawdown_pct,
            initial_equity=config.total_capital_usdt,
        )

        # 初始仓位
        self.spot_pos = Position(
            symbol=config.symbol_spot,
            market=MarketType.SPOT,
            quantity=config.spot_initial_btc,
            avg_entry=center,
        )
        self.perp_pos = Position(
            symbol=config.symbol_perp,
            market=MarketType.PERP,
            quantity=-config.short_initial_btc,  # 空 = 负
            avg_entry=center,
        )

        # 现金 = 总本金 - 初始现货成本
        self.cash = config.total_capital_usdt - config.spot_initial_btc * center
        self.funding_paid = _ZERO
        self.total_fees = _ZERO
        self.n_trades = 0

        # Recenter 状态
        # 注意: 自己 track center, 不从 grid bounds 推断, 因为 grid lower/upper 经
        # floor/ceil 取整后 (lower+upper)/2 != actual recenter center
        self.center: Decimal = center
        self.n_recenters = 0
        self.last_recenter_ts: Optional[datetime] = None
        self.recenter_events: list[RecenterEvent] = []

    # ------------------------------------------------------------------
    # on_tick - 每 tick 入口
    # ------------------------------------------------------------------

    def on_tick(
        self,
        market: MarketState,
        margin_ratio: Decimal = Decimal("1.0"),
    ) -> list[OrderIntent]:
        """每个 tick 调用一次, 返回需要下单的意图列表。

        顺序:
          1. mark-to-market 更新仓位
          2. maybe_recenter (可能 rebuild grid + reset trend)
          3. funding 结算 (若 funding_settled=True)
          4. 风控评估 → EMERGENCY 直接 return []
          5. trend filter halt → return []
          6. grid.update_price 触发
          7. 生成 paired_inverse intents
        """
        # 1. mark
        self.spot_pos.mark_to_market(market.spot_price)
        self.perp_pos.mark_to_market(market.perp_price)

        # 2. recenter (在 grid trigger 前; 可能 rebuild)
        self.maybe_recenter(market.timestamp, market.spot_price)

        # 3. funding (若标记)
        if market.funding_settled:
            self.apply_funding(market)

        # snapshot
        snapshot = self.get_snapshot(market)

        # 4. 风控
        risk_report = self.risk.evaluate(
            snapshot=snapshot,
            market=market,
            consecutive_trend_grids=self.grid.consecutive_direction_grids,
            margin_ratio=margin_ratio,
        )
        if risk_report.should_pause:
            logger.warning(f"dgr_btc 风控停机: {risk_report.messages}")
            return []

        # 5. grid 触发 (必须在 trend filter check 之前调用, 否则 trend halt 期间
        # grid 状态永不更新, 反向也无法 reset → 死锁。mirror Codex strategy.on_tick 顺序)
        trigger = self.grid.update_price(market.spot_price)
        if trigger is None:
            return []

        # 6. trend filter (⭐ 核心 alpha 保护)
        if self.grid.is_trending(self.config.risk_trend_grids_threshold):
            logger.info(
                "dgr_btc 趋势保护激活 consecutive=%d threshold=%d",
                self.grid.consecutive_direction_grids,
                self.config.risk_trend_grids_threshold,
            )
            return []

        # 7. 生成 intents
        defensive = risk_report.level == RiskLevel.DEFENSIVE
        return self._generate_intents(trigger, defensive)

    # ------------------------------------------------------------------
    # Recenter (doc §9 + §10)
    # ------------------------------------------------------------------

    def maybe_recenter(self, ts: datetime, price: Decimal) -> Optional[RecenterEvent]:
        """检查是否触发 recenter, 若触发则 rebuild grid。

        触发条件 (doc §9):
          1. recenter_enabled = True
          2. |price - center|/center >= recenter_trigger_pct
          3. 距上次 recenter >= recenter_cooldown_sec
        """
        if not self.config.recenter_enabled:
            return None
        if not self.config.dynamic_bounds_enabled:
            return None
        if self.center <= 0:
            return None

        deviation_pct = abs(price - self.center) / self.center
        if deviation_pct < self.config.recenter_trigger_pct:
            return None

        # cooldown
        if self.last_recenter_ts is not None:
            elapsed = (ts - self.last_recenter_ts).total_seconds()
            if elapsed < self.config.recenter_cooldown_sec:
                return None

        # 执行 recenter (doc §10)
        old_center = self.center
        self.grid.rebuild_around(
            new_center=price,
            width_pct=self.config.width_pct,
        )
        # ⭐ 同步 strategy.center (grid bounds 取整后不能反推)
        self.center = price
        event = RecenterEvent(
            timestamp=ts,
            old_center=old_center,
            new_center=price,
            deviation_pct=deviation_pct,
        )
        self.recenter_events.append(event)
        self.n_recenters += 1
        self.last_recenter_ts = ts
        logger.info(
            "dgr_btc 再定心 #%d: %s → %s (偏离 %.2f%%)",
            self.n_recenters,
            old_center,
            price,
            float(deviation_pct * 100),
        )
        return event

    # ------------------------------------------------------------------
    # 意图生成 (paired_inverse - doc §5 §6 §8 严禁修改)
    # ------------------------------------------------------------------

    def _generate_intents(
        self,
        trigger: GridTrigger,
        defensive: bool,
    ) -> list[OrderIntent]:
        """生成订单意图 - paired_inverse:
        - 价格上穿: 卖现货 + 加空 (Delta 下降)
        - 价格下穿: 买现货 + 平空 (Delta 上升)
        - Delta ±limit 双向硬刹车: 触限整对跳过 (停手等价格反向)
        """
        intents: list[OrderIntent] = []
        qty = self.config.grid_qty_per_grid

        for grid_price in trigger.crossed_grids:
            if trigger.direction == Side.SELL:  # 上穿 → 卖现货 + 加空
                intents.extend(self._gen_sell_pair(grid_price, qty, defensive))
            else:  # 下穿 → 买现货 + 平空
                intents.extend(self._gen_buy_pair(grid_price, qty, defensive))
        return intents

    def _gen_sell_pair(
        self,
        grid_price: Decimal,
        qty: Decimal,
        defensive: bool,
    ) -> list[OrderIntent]:
        """上穿（价格上涨）: 卖现货 + 加空。两条腿都使 Delta 下降。

        Delta 硬刹车: 若操作会使 Delta 跌破 lower_limit, 整对跳过。
        """
        out: list[OrderIntent] = []
        eps = Decimal("0.00000001")

        # 现货必须够卖
        if self.spot_pos.quantity - qty < -eps:
            return out

        # 加空腿: 需满足空单上限 + 非防御模式
        new_short = abs(self.perp_pos.quantity) + qty
        can_add_short = (
            not defensive
            and new_short <= self.config.max_short_btc + eps
        )

        # 预测对 Delta 影响
        new_spot = self.spot_pos.quantity - qty
        predicted_short = (
            new_short if can_add_short else abs(self.perp_pos.quantity)
        )
        new_delta = new_spot - predicted_short

        # 硬刹车
        if new_delta < self.config.delta_lower_limit - eps:
            return out

        # 两条腿
        out.append(
            OrderIntent(
                market=MarketType.SPOT,
                side=Side.SELL,
                price=grid_price,
                quantity=qty,
                grid_level=grid_price,
                reason=f"grid_up_{grid_price}",
            )
        )
        if can_add_short:
            out.append(
                OrderIntent(
                    market=MarketType.PERP,
                    side=Side.SELL,
                    price=grid_price,
                    quantity=qty,
                    grid_level=grid_price,
                    reason=f"add_short_{grid_price}",
                )
            )
        return out

    def _gen_buy_pair(
        self,
        grid_price: Decimal,
        qty: Decimal,
        defensive: bool,
    ) -> list[OrderIntent]:
        """下穿（价格下跌）: 买现货 + 平空。两条腿都使 Delta 上升。

        Delta 硬刹车: 若操作会使 Delta 突破 upper_limit, 整对跳过。
        """
        out: list[OrderIntent] = []
        eps = Decimal("0.00000001")

        # 现货上限
        if self.spot_pos.quantity + qty > self.config.max_spot_btc + eps:
            return out

        # 平空腿: 需有足量空单
        can_close_short = (
            self.perp_pos.quantity < 0
            and abs(self.perp_pos.quantity) >= qty - eps
        )

        # 预测对 Delta 影响
        new_spot = self.spot_pos.quantity + (qty if not defensive else _ZERO)
        new_short = abs(self.perp_pos.quantity)
        if can_close_short:
            new_short = new_short - qty
        new_delta = new_spot - new_short

        # 硬刹车
        if new_delta > self.config.delta_upper_limit + eps:
            return out

        # 两条腿
        if not defensive:
            out.append(
                OrderIntent(
                    market=MarketType.SPOT,
                    side=Side.BUY,
                    price=grid_price,
                    quantity=qty,
                    grid_level=grid_price,
                    reason=f"grid_dn_{grid_price}",
                )
            )
        if can_close_short:
            out.append(
                OrderIntent(
                    market=MarketType.PERP,
                    side=Side.BUY,
                    price=grid_price,
                    quantity=qty,
                    grid_level=grid_price,
                    reason=f"close_short_{grid_price}",
                )
            )
        return out

    # ------------------------------------------------------------------
    # 成交回调 + funding
    # ------------------------------------------------------------------

    def on_trade(self, trade: Trade) -> None:
        """成交回调, 更新仓位 + cash + fees。"""
        if trade.market == MarketType.SPOT:
            self.spot_pos.update_on_trade(trade)
            self.cash += trade.net_value
        else:
            self.perp_pos.update_on_trade(trade)
            # 永续不直接占用 cash (仅保证金), 实现盈亏 by perp.realized_pnl 累计
        self.total_fees += trade.fee
        self.n_trades += 1

    def apply_funding(self, market: MarketState) -> Decimal:
        """Funding 结算（每 8h）. 正费率: 空头收钱; 负费率: 空头付钱."""
        if self.perp_pos.quantity == 0:
            return _ZERO
        notional = abs(self.perp_pos.quantity) * market.perp_price
        payment = market.funding_rate * notional
        if self.perp_pos.quantity < 0:  # 空
            self.cash += payment
            self.funding_paid += payment
        else:  # 多
            self.cash -= payment
            self.funding_paid -= payment
        return payment

    # ------------------------------------------------------------------
    # 快照
    # ------------------------------------------------------------------

    def get_snapshot(self, market: MarketState) -> PortfolioSnapshot:
        """组合净值 = cash + spot 市值 + perp 盈亏 (已实现+未实现)。

        注: 不加 spot.realized_pnl, 因为现货实现盈亏已经体现在 cash 变动中。
        """
        spot_value = self.spot_pos.quantity * market.spot_price
        equity = (
            self.cash
            + spot_value
            + self.perp_pos.unrealized_pnl
            + self.perp_pos.realized_pnl
        )
        if self.perp_pos.quantity < 0:
            delta = self.spot_pos.quantity - abs(self.perp_pos.quantity)
        else:
            delta = self.spot_pos.quantity + self.perp_pos.quantity

        # Phase E.3: spot/perp basis
        spot_mk = market.spot_price
        perp_mk = market.perp_price
        basis_bps_val = (
            (perp_mk - spot_mk) / spot_mk * Decimal(10000)
            if spot_mk > 0 else Decimal(0)
        )
        return PortfolioSnapshot(
            timestamp=market.timestamp,
            spot_position=self.spot_pos,
            perp_position=self.perp_pos,
            cash_usdt=self.cash,
            mark_price=spot_mk,  # 兼容
            delta=delta,
            total_equity=equity,
            funding_paid=self.funding_paid,
            total_fees=self.total_fees,
            n_trades=self.n_trades,
            spot_mark_price=spot_mk,
            perp_mark_price=perp_mk,
            basis_bps=basis_bps_val,
        )
