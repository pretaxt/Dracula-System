"""
backtest/dgr_engine.py
======================
dgr_btc (#13) 回测引擎 — K 线驱动 DgrBtcStrategy.on_tick()。

设计原则:
  - 不重写策略逻辑（avoid Phase A vs B 分歧）— DgrBtcStrategy 作 SUT
  - 接收 pandas DataFrame klines (timestamp/open/high/low/close[/volume])
    + funding DataFrame (fundingTime/fundingRate) — 与 Codex 引擎一致
  - 撮合: maker_only 模式 (复现 Codex W0 +42.76% 必需) 或 maker_ratio 概率模式
  - funding: 在 candle ts 跨过 next_funding_event 时通过 MarketState.funding_settled=True 标记
    → strategy 内部 apply_funding

成交模型 (maker_only):
  - 触发价 = grid_price (LIMIT_MAKER 预挂)
  - is_maker=True, slippage=0
  - fee_rate: spot 用 backtest_spot_fee, perp 用 backtest_perp_maker_fee
  → 与 Codex BacktestEngine._simulate_fill 一致

复现 W0 用法:
    cfg = DgrBtcStrategyConfig.from_yaml({...})  # doc 默认配置
    engine = DgrBtcBacktestEngine(cfg, klines_df, funding_df, maker_only=True)
    result = engine.run()
    print(result.summary())  # 应得 return_pct = 42.76 / n_trades = 47364 / n_recenters = 6
"""
from __future__ import annotations

import random
import uuid as uuid_lib
from decimal import Decimal
from typing import Iterable, Optional

import pandas as pd

from app.backtest.dgr_models import (
    DgrBtcBacktestResult,
    EquityPoint,
    RecenterRecord,
)
from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.strategy_core import DgrBtcStrategy, OrderIntent
from app.strategies.dgr_btc.types import (
    MarketState,
    MarketType,
    Side,
    Trade,
)


_ZERO = Decimal("0")
_BPS = Decimal("10000")


class DgrBtcBacktestEngine:
    """K 线驱动 DgrBtcStrategy."""

    def __init__(
        self,
        config: DgrBtcStrategyConfig,
        klines: pd.DataFrame,
        funding: pd.DataFrame | None = None,
        maker_only: bool = True,
        rng_seed: int = 42,
        snapshot_every: int = 10,
        perp_klines: pd.DataFrame | None = None,
        # Phase H 实盘对齐参数 (LIVE-mirror modeling):
        maker_reject_rate: float = 0.0,    # post_only_crossed 概率 (0=完美 maker, 0.10=10% reject)
        perp_basis_pct: float = 0.0,       # perp 相对 spot 价差 (-0.001=-0.1%, perp 略低)
        intrabar_mode: bool = False,       # True=用 high/low/close 三次 evaluate (近似 intra-bar 穿格)
    ):
        """
        Args:
            config: DgrBtcStrategyConfig (yaml 加载或 default).
            klines: DataFrame with columns ['timestamp', 'open', 'high', 'low', 'close', 'volume'].
                timestamp must be datetime-coercible.
            funding: Optional DataFrame with ['fundingTime', 'fundingRate']. None → 0 funding.
            maker_only: 复现 Codex 必需 (穿越即成交, 100% maker, 无 slippage).
            rng_seed: maker_ratio 模式下的随机种子.
            snapshot_every: 每 N 根 K 记一次 equity 点 (与 Codex `if i % 10 == 0` 一致).
        """
        self.config = config
        self.klines = klines.copy()
        # 规范化 timestamp 列
        if not pd.api.types.is_datetime64_any_dtype(self.klines["timestamp"]):
            self.klines["timestamp"] = pd.to_datetime(self.klines["timestamp"])
        self.klines = self.klines.sort_values("timestamp").reset_index(drop=True)

        # funding
        if funding is not None and len(funding) > 0:
            f = funding.copy()
            if not pd.api.types.is_datetime64_any_dtype(f["fundingTime"]):
                f["fundingTime"] = pd.to_datetime(f["fundingTime"])
            self.funding = f.sort_values("fundingTime").reset_index(drop=True)
        else:
            self.funding = None

        # Phase E.3: perp_klines (basis-aware) — fallback 用 spot price 保持 byte-equal
        if perp_klines is not None and len(perp_klines) > 0:
            pk = perp_klines.copy()
            if not pd.api.types.is_datetime64_any_dtype(pk["timestamp"]):
                pk["timestamp"] = pd.to_datetime(pk["timestamp"])
            pk = pk.sort_values("timestamp").reset_index(drop=True)
            # merge_asof to align perp close to spot timestamp
            merged = pd.merge_asof(
                self.klines[["timestamp"]],
                pk[["timestamp", "close"]].rename(columns={"close": "perp_close"}),
                on="timestamp",
                direction="nearest",
                tolerance=pd.Timedelta("2min"),
            )
            self._perp_prices = merged["perp_close"].values  # may have NaN
            self.has_perp_data = True
        else:
            self._perp_prices = None
            self.has_perp_data = False

        self.maker_only = maker_only
        self._rng = random.Random(rng_seed)
        self.snapshot_every = snapshot_every

        # 用首根 K 的 close 作 start_price 创建 strategy
        if len(self.klines) == 0:
            raise ValueError("klines 为空")
        first_price = Decimal(str(self.klines["close"].iloc[0]))
        self.strategy = DgrBtcStrategy(config, start_price=first_price)

        # 回测专用 fee / slip
        self._spot_fee = config.backtest_spot_fee
        self._perp_maker_fee = config.backtest_perp_maker_fee
        self._perp_taker_fee = config.backtest_perp_taker_fee
        self._slippage_bps = Decimal(str(config.backtest_slippage_bps))
        self._maker_ratio = config.backtest_maker_ratio

        # 触发统计
        self._n_grid_triggers = 0
        self._n_trend_pauses = 0

        # Phase H LIVE-mirror modeling
        self._maker_reject_rate = float(maker_reject_rate)
        self._perp_basis_factor = Decimal(str(1.0 + perp_basis_pct))  # perp_price = spot * factor
        self._intrabar_mode = bool(intrabar_mode)
        self._n_maker_rejected = 0

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def run(self) -> DgrBtcBacktestResult:
        n = len(self.klines)

        # 准备 funding 迭代器（与 Codex 一致: 按 fundingTime advance）
        funding_iter = None
        next_funding: Optional[tuple] = None  # (ts, rate)
        if self.funding is not None:
            funding_iter = iter(
                zip(
                    self.funding["fundingTime"],
                    self.funding["fundingRate"].astype(float),
                )
            )
            next_funding = next(funding_iter, None)

        # 初始 equity (第 1 根 K 的 close 作 mark)
        first_ts = self.klines["timestamp"].iloc[0]
        first_price = Decimal(str(self.klines["close"].iloc[0]))
        self.strategy.spot_pos.mark_to_market(first_price)
        self.strategy.perp_pos.mark_to_market(first_price)
        initial_market = MarketState(
            timestamp=first_ts,
            spot_price=first_price,
            perp_price=first_price,
        )
        initial_snap = self.strategy.get_snapshot(initial_market)
        initial_equity = initial_snap.total_equity

        result = DgrBtcBacktestResult(
            config_snapshot=self._dump_config(),
            initial_equity=initial_equity,
        )

        prev_grid_count = self.strategy.grid.consecutive_direction_grids

        # Phase H.live FULL REFACTOR: pending orders 池模拟 LIVE pre-placement
        # 状态: 持久化的"挂单"列表 — 每个元素是 dict
        #   {market, side, price, qty, grid_level (= spot_level), placed_at}
        # 每根 K 线流程:
        #   1) housekeeping: strategy.on_tick(close_market) 更新 grid_index/center/funding
        #      → 忽略 emitted intents (反应式 emit 不 mirror LIVE)
        #   2) recenter/trend halt 检测 → cancel ALL pending
        #   3) maintain pre-placed pairs (mirror _maintain_resting_pairs):
        #      算 spot_upper/lower + perp_level = spot_level ± maker_safety$20,
        #      检查 cap (max_spot/max_short, delta band), 补 missing legs
        #   4) Phase A: scan pending → 若 K 线 [low, high] 覆盖 order.price → fill at price
        #      顺序: open 距 high/low 远端先撞 (模拟 sub-bar swing 时序)
        #   5) strategy.on_trade(trade) 更新 strategy state
        #   6) snapshot
        pending_orders: list = []   # list[dict]
        last_center: Decimal | None = None
        maker_safety = Decimal("20")  # mirror LIVE _maintain_resting_pairs
        order_seq = 0

        def make_pending(market_, side_, price_, qty_, grid_level_, ts_):
            nonlocal order_seq
            order_seq += 1
            return {
                "id": f"bt_p{order_seq}",
                "market": market_,
                "side": side_,
                "price": price_,
                "qty": qty_,
                "grid_level": grid_level_,
                "placed_at": ts_,
            }

        for i, row in enumerate(self.klines.itertuples(index=False)):
            ts = row.timestamp
            close_price = Decimal(str(row.close))
            open_price = Decimal(str(row.open))
            high_price = Decimal(str(row.high))
            low_price = Decimal(str(row.low))

            # ---- funding 检查 ----
            funding_rate = _ZERO
            funding_settled = False
            while next_funding is not None and ts >= next_funding[0]:
                funding_rate = Decimal(str(next_funding[1]))
                funding_settled = True
                next_funding = next(funding_iter, None)

            # ---- 构造 perp price for this bar ----
            if self.has_perp_data and self._perp_prices is not None:
                pp = self._perp_prices[i]
                if pp == pp:
                    perp_price_for_ts = Decimal(str(pp))
                else:
                    perp_price_for_ts = close_price * self._perp_basis_factor
            else:
                perp_price_for_ts = close_price * self._perp_basis_factor

            market = MarketState(
                timestamp=ts,
                spot_price=close_price,
                perp_price=perp_price_for_ts,
                funding_rate=funding_rate,
                bid_depth_usdt=Decimal("10000000"),
                ask_depth_usdt=Decimal("10000000"),
                realized_vol_1h=_ZERO,
                funding_settled=funding_settled,
            )

            # ---- STEP 1: housekeeping — strategy.on_tick 但 ignore intents ----
            _ = self.strategy.on_tick(market, margin_ratio=Decimal("1.0"))
            cur_grid_count = self.strategy.grid.consecutive_direction_grids
            if cur_grid_count != prev_grid_count:
                self._n_grid_triggers += 1
            prev_grid_count = cur_grid_count

            # ---- STEP 2: recenter / trend halt → cancel ALL pending ----
            is_trending = self.strategy.grid.is_trending(
                self.config.risk_trend_grids_threshold
            )
            center = self.strategy.center
            cancel_all = False
            if last_center is not None and last_center != center:
                cancel_all = True  # recenter
            if is_trending:
                cancel_all = True
                self._n_trend_pauses += 1
            if cancel_all:
                pending_orders.clear()
            last_center = center

            # ---- STEP 3: maintain — 补充 missing pre-placed legs ----
            # 仅在非 trend halt + 非 recenter 后才挂新单 (LIVE 一致)
            if not is_trending:
                step = self.config.grid_step_usdt
                qty = self.config.grid_qty_per_grid
                spot_upper = center + step
                spot_lower = center - step
                perp_upper = spot_upper + maker_safety
                perp_lower = spot_lower - maker_safety
                spot_qty = self.strategy.spot_pos.quantity
                perp_qty_abs = abs(self.strategy.perp_pos.quantity)
                upper_ok = (
                    spot_qty - qty >= _ZERO
                    and perp_qty_abs + qty <= self.config.max_short_btc
                )
                lower_ok = (
                    spot_qty + qty <= self.config.max_spot_btc
                    and perp_qty_abs - qty >= _ZERO
                )
                # 已存在的 (market, side, grid_level) set
                existing = {
                    (o["market"], o["side"], str(o["grid_level"]))
                    for o in pending_orders
                }
                if upper_ok:
                    if ("SPOT", "SELL", str(spot_upper)) not in existing:
                        pending_orders.append(make_pending(
                            "SPOT", "SELL", spot_upper, qty, spot_upper, ts))
                    if ("PERP", "SELL", str(spot_upper)) not in existing:
                        pending_orders.append(make_pending(
                            "PERP", "SELL", perp_upper, qty, spot_upper, ts))
                if lower_ok:
                    if ("SPOT", "BUY", str(spot_lower)) not in existing:
                        pending_orders.append(make_pending(
                            "SPOT", "BUY", spot_lower, qty, spot_lower, ts))
                    if ("PERP", "BUY", str(spot_lower)) not in existing:
                        pending_orders.append(make_pending(
                            "PERP", "BUY", perp_lower, qty, spot_lower, ts))

            # ---- STEP 4: scan pending — K 线 [low, high] 覆盖则 fill ----
            # 顺序: open 距 high/low 远端先撞 (大 swing 优先)
            # SELL fill 条件: high >= price
            # BUY  fill 条件: low  <= price
            # 注: spot/perp 的 high/low 用同一 spot K 线 (合理近似, perp 跟 spot ±0.1% 内)
            up_swing = high_price - open_price
            down_swing = open_price - low_price
            sub_order = ["up", "down"] if up_swing >= down_swing else ["down", "up"]
            still_pending = []
            for o in pending_orders:
                if o["side"] == "SELL" and high_price >= o["price"]:
                    fill_phase = "up"
                elif o["side"] == "BUY" and low_price <= o["price"]:
                    fill_phase = "down"
                else:
                    still_pending.append(o)
                    continue
                # LIVE-mirror: maker_reject_rate per_leg (post_only_crossed)
                if self._maker_reject_rate > 0 and self._rng.random() < self._maker_reject_rate:
                    self._n_maker_rejected += 1
                    # reject 后单从 binance 上 cancel, 等下个 tick maintain 重挂
                    continue
                # synthesize fill at maker price (= o["price"])
                from app.strategies.dgr_btc.types import MarketType, Side
                m_enum = MarketType.SPOT if o["market"] == "SPOT" else MarketType.PERP
                s_enum = Side.BUY if o["side"] == "BUY" else Side.SELL
                fee_rate = (
                    self._spot_fee if m_enum == MarketType.SPOT
                    else self._perp_maker_fee
                )
                notional = o["price"] * o["qty"]
                fee = notional * fee_rate
                trade = Trade(
                    trade_id=f"bt_{uuid_lib.uuid4().hex[:12]}",
                    order_id=o["id"],
                    symbol=(self.config.symbol_spot if m_enum == MarketType.SPOT
                            else self.config.symbol_perp),
                    market=m_enum,
                    side=s_enum,
                    price=o["price"],
                    quantity=o["qty"],
                    fee=fee,
                    is_maker=True,
                    timestamp=ts,
                    grid_level=o["grid_level"],
                )
                self.strategy.on_trade(trade)
                result.trades.append(trade)
                # fill 后单从 pending 移除 (不加 still_pending)

            pending_orders = still_pending

            # 使用 close 作为 snapshot 价格
            price = close_price

            # 每 snapshot_every 根记 equity (与 Codex `if i % 10 == 0` 一致)
            if i % self.snapshot_every == 0 or i == n - 1:
                snap = self.strategy.get_snapshot(market)
                result.equity_curve.append(
                    EquityPoint(
                        timestamp=ts,
                        equity_usdt=snap.total_equity,
                        cash=snap.cash_usdt,
                        spot_qty=snap.spot_position.quantity,
                        perp_qty=snap.perp_position.quantity,
                        delta=snap.delta,
                        mark_price=price,
                    )
                )

        # 终态
        last_row = self.klines.iloc[-1]
        last_price = Decimal(str(last_row["close"]))
        last_ts = last_row["timestamp"]
        self.strategy.spot_pos.mark_to_market(last_price)
        self.strategy.perp_pos.mark_to_market(last_price)
        final_market = MarketState(
            timestamp=last_ts,
            spot_price=last_price,
            perp_price=last_price,
        )
        final_snap = self.strategy.get_snapshot(final_market)
        result.final_equity = final_snap.total_equity
        result.funding_paid = self.strategy.funding_paid
        result.total_fees = self.strategy.total_fees
        result.n_grid_triggers = self._n_grid_triggers
        result.n_trend_pauses = self._n_trend_pauses
        result.final_spot_pos = self.strategy.spot_pos
        result.final_perp_pos = self.strategy.perp_pos

        # recenter 镜像
        for ev in self.strategy.recenter_events:
            result.recenters.append(
                RecenterRecord(
                    timestamp=ev.timestamp,
                    old_center=ev.old_center,
                    new_center=ev.new_center,
                    deviation_pct=ev.deviation_pct,
                )
            )

        return result

    # ------------------------------------------------------------------
    # 撮合
    # ------------------------------------------------------------------

    def _simulate_fill(
        self, intent: OrderIntent, tick: MarketState,
        bar_low: Decimal | None = None, bar_high: Decimal | None = None,
    ) -> Trade | None:
        """模拟 fill.

        Phase H LIVE-mirror:
          - maker_reject_rate > 0 时按概率模拟 post_only_crossed → 返回 None（实盘 reject 后 cancel paired leg）
          - maker_only=True 时仍 100% maker fill；reject 走单独分支
          - Phase H.live fix #2: 物理可达性检查 — intent.price 必须落在 K 线 [low, high] 范围内.
            LIVE maker 单 fill 要求价格穿越挂单价: SELL 要求 high ≥ intent.price (taker 主动 BUY 上去),
            BUY 要求 low ≤ intent.price (taker 主动 SELL 下来). 不满足则永远 fill 不到.
            没传 bar_low/high 时(legacy 调用)跳过此 check 保 backward compat.
        """
        # LIVE-mirror: maker reject (per_leg 独立判断)
        if self._maker_reject_rate > 0 and self._rng.random() < self._maker_reject_rate:
            self._n_maker_rejected += 1
            return None
        # Phase H.live fix #2: 物理可达性 (K 线 high/low 必须穿过 intent.price)
        if bar_low is not None and bar_high is not None:
            if intent.side == Side.SELL and bar_high < intent.price:
                return None  # SELL maker: 价格没涨到挂单价
            if intent.side == Side.BUY and bar_low > intent.price:
                return None  # BUY maker: 价格没跌到挂单价

        if self.maker_only:
            is_maker = True
            fill_price = intent.price
        else:
            is_maker = self._rng.random() < float(self._maker_ratio)
            slip = (self._slippage_bps / _BPS) if not is_maker else _ZERO
            if intent.side == Side.BUY:
                fill_price = intent.price * (Decimal("1") + slip)
            else:
                fill_price = intent.price * (Decimal("1") - slip)

        if intent.market == MarketType.SPOT:
            fee_rate = self._spot_fee
        else:
            fee_rate = (
                self._perp_maker_fee if is_maker else self._perp_taker_fee
            )

        notional = fill_price * intent.quantity
        fee = notional * fee_rate
        return Trade(
            trade_id=f"bt_{uuid_lib.uuid4().hex[:12]}",
            order_id=f"intent_{uuid_lib.uuid4().hex[:8]}",
            symbol=(
                self.config.symbol_spot
                if intent.market == MarketType.SPOT
                else self.config.symbol_perp
            ),
            market=intent.market,
            side=intent.side,
            price=fill_price,
            quantity=intent.quantity,
            fee=fee,
            is_maker=is_maker,
            timestamp=tick.timestamp,
            grid_level=intent.grid_level,
        )

    # ------------------------------------------------------------------
    # Config dump
    # ------------------------------------------------------------------

    def _dump_config(self) -> dict:
        c = self.config
        return {
            "instance_name": c.instance_name,
            "symbol_spot": c.symbol_spot,
            "symbol_perp": c.symbol_perp,
            "total_capital_usdt": str(c.total_capital_usdt),
            "spot_initial_btc": str(c.spot_initial_btc),
            "short_initial_btc": str(c.short_initial_btc),
            "grid_step_usdt": str(c.grid_step_usdt),
            "grid_qty_per_grid": str(c.grid_qty_per_grid),
            "width_pct": str(c.width_pct),
            "recenter_trigger_pct": str(c.recenter_trigger_pct),
            "recenter_cooldown_sec": c.recenter_cooldown_sec,
            "delta_upper_limit": str(c.delta_upper_limit),
            "delta_lower_limit": str(c.delta_lower_limit),
            "max_spot_btc": str(c.max_spot_btc),
            "max_short_btc": str(c.max_short_btc),
            "leverage": c.leverage,
            "trend_grids_threshold": c.risk_trend_grids_threshold,
            "spot_fee": str(c.backtest_spot_fee),
            "perp_maker_fee": str(c.backtest_perp_maker_fee),
            "maker_only": self.maker_only,
        }
