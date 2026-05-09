"""#04 期现套利回测引擎

按时间序列遍历 ``BasisSnapshot``，对每个 (timestamp, symbol) 应用与生产
``SpotPerpPaperSession`` 相同的入场/退出决策，在内存中模拟交易。

不写 DB、不调 broker，纯计算。开/平仓应用对称的 fee + slippage 成本，
盈亏按 delta-neutral 双腿真实公式算（spot_leg + perp_leg − fees）。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from app.backtest.spot_perp_models import (
    BasisSnapshot,
    SpotPerpBacktestConfig,
    SpotPerpBacktestResult,
    SpotPerpEquityPoint,
    SpotPerpTrade,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_ZERO = Decimal("0")


class SpotPerpBacktestEngine:
    """spot_perp 回测引擎。"""

    def __init__(self, config: SpotPerpBacktestConfig) -> None:
        self._cfg = config
        # 开仓中：{symbol: SpotPerpTrade}（同 symbol 不重复开）
        self._open: dict[str, SpotPerpTrade] = {}
        # 已结束交易（含每笔完整生命周期）
        self._closed: list[SpotPerpTrade] = []
        # 滑窗：{symbol: [(timestamp, abs_basis_pct), ...]}（peak dropoff 用）
        self._peak_cache: dict[str, list[tuple[datetime, Decimal]]] = defaultdict(list)
        # 拒绝/跳过计数
        self._rejected = 0
        self._skipped = 0
        # 资金曲线
        self._equity_curve: list[SpotPerpEquityPoint] = []
        self._equity = Decimal(str(config.initial_capital_usd))
        self._realized_pnl = _ZERO

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self, snapshots: Iterable[BasisSnapshot]) -> SpotPerpBacktestResult:
        """按时间排序后遍历快照执行回测。

        snapshots 可以是任意可迭代；引擎会全量物化后排序。
        """
        sorted_snaps = sorted(snapshots, key=lambda s: s.timestamp)
        # 按 timestamp 分组（同一时刻可能有多个 symbol）
        by_ts: dict[datetime, list[BasisSnapshot]] = defaultdict(list)
        for s in sorted_snaps:
            by_ts[s.timestamp].append(s)

        for ts in sorted(by_ts.keys()):
            snaps_at_ts = by_ts[ts]
            # 1. 更新 peak cache（所有快照都进窗口）
            self._update_peak_cache(snaps_at_ts, ts)
            # 2. 检查已开仓位的退出条件（先 close 再 open，腾 slot）
            for snap in snaps_at_ts:
                self._maybe_close(snap, ts)
            # 3. 检查新开仓机会
            for snap in snaps_at_ts:
                self._maybe_open(snap, ts)
            # 4. 记录资金曲线
            self._record_equity(ts, snaps_at_ts)

        # 强制平掉所有剩余持仓（用最后一笔快照价格）
        if sorted_snaps:
            last_ts = sorted_snaps[-1].timestamp
            last_snaps = {s.symbol: s for s in sorted_snaps if s.timestamp == last_ts}
            for sym, trade in list(self._open.items()):
                snap = last_snaps.get(sym)
                if snap is None:
                    # 退化：用最后扫到的同 symbol 任意快照
                    candidate = next(
                        (s for s in reversed(sorted_snaps) if s.symbol == sym),
                        None,
                    )
                    if candidate is None:
                        continue
                    snap = candidate
                self._close_trade(trade, snap, "final_force_close")

        # 构造结果
        result = SpotPerpBacktestResult(
            config=self._cfg,
            trades=self._closed,
            equity_curve=self._equity_curve,
            rejected_count=self._rejected,
            skipped_count=self._skipped,
        )
        return result

    # ------------------------------------------------------------------
    # 入场
    # ------------------------------------------------------------------

    def _maybe_open(self, snap: BasisSnapshot, ts: datetime) -> None:
        # 已持有同 symbol 跳过
        if snap.symbol in self._open:
            return
        # 容量上限
        if len(self._open) >= self._cfg.max_concurrent:
            return
        # 方向过滤
        df = self._cfg.direction_filter
        if df != "both" and snap.direction != df:
            return

        # APR/基差阈值
        threshold = self._cfg.entry_threshold(snap.direction)
        abs_basis = abs(snap.basis_pct)
        if abs_basis < threshold:
            self._skipped += 1
            return

        # 入场时机过滤（防接飞刀）
        if not self._check_peak_dropoff(snap.symbol, abs_basis, ts):
            self._rejected += 1
            return

        # 开仓 — 应用 fee + slippage
        notional = self._cfg.notional_per_position
        # 滑点：开仓时不利方向（spot 买价 + slip / perp 卖价 - slip）
        slip = self._cfg.slippage_pct / Decimal("100")
        if snap.direction == "premium":
            entry_spot = snap.spot_price * (Decimal("1") + slip)
            entry_perp = snap.perp_price * (Decimal("1") - slip)
        else:  # discount
            entry_spot = snap.spot_price * (Decimal("1") - slip)
            entry_perp = snap.perp_price * (Decimal("1") + slip)
        # 4 腿手续费 = 2 × notional × fee_rate × 2（开+平共 4 笔）
        # 这里只先扣开仓 2 腿 fee；平仓时再扣 2 腿
        open_fees = notional * self._cfg.fee_rate * Decimal("2")

        trade = SpotPerpTrade(
            symbol=snap.symbol,
            exchange=snap.exchange,
            direction=snap.direction,
            notional_usd=notional,
            entry_time=ts,
            entry_basis_pct=snap.basis_pct,
            entry_spot=entry_spot,
            entry_perp=entry_perp,
            fees_paid=open_fees,
        )
        self._open[snap.symbol] = trade

    # ------------------------------------------------------------------
    # 退出
    # ------------------------------------------------------------------

    def _maybe_close(self, snap: BasisSnapshot, ts: datetime) -> None:
        trade = self._open.get(snap.symbol)
        if trade is None:
            return

        # 持仓时长
        held = ts - trade.entry_time
        held_minutes = Decimal(str(held.total_seconds() / 60))
        held_hours = held_minutes / Decimal("60")
        min_hold_passed = held_minutes >= self._cfg.min_hold_minutes

        current_basis = snap.basis_pct
        abs_current = abs(current_basis)

        exit_reason: str | None = None
        # 优先级：basis_convergence > basis_stop > max_hold
        if abs_current <= self._cfg.exit_pct and min_hold_passed:
            exit_reason = "basis_convergence"
        else:
            # basis 扩大止损（方向感知）
            entry_basis = trade.entry_basis_pct
            if entry_basis > 0:
                widening = current_basis - entry_basis
            elif entry_basis < 0:
                widening = entry_basis - current_basis
            else:
                widening = _ZERO
            if (
                self._cfg.stop_basis_widening_pct > 0
                and widening >= self._cfg.stop_basis_widening_pct
                and min_hold_passed
            ):
                exit_reason = "basis_stop"
            elif held_hours >= self._cfg.max_hold_hours:
                exit_reason = "max_hold"

        if exit_reason is None:
            return
        self._close_trade(trade, snap, exit_reason)

    def _close_trade(
        self, trade: SpotPerpTrade, snap: BasisSnapshot, exit_reason: str,
    ) -> None:
        # 平仓滑点：相反方向不利
        slip = self._cfg.slippage_pct / Decimal("100")
        if trade.direction == "premium":
            close_spot = snap.spot_price * (Decimal("1") - slip)  # 卖现货
            close_perp = snap.perp_price * (Decimal("1") + slip)  # 买回 perp
        else:
            close_spot = snap.spot_price * (Decimal("1") + slip)  # 买回现货
            close_perp = snap.perp_price * (Decimal("1") - slip)  # 卖 perp

        # 双腿盈亏（按 notional / entry_spot 算 base size）
        if trade.entry_spot <= 0:
            spot_size = _ZERO
        else:
            spot_size = trade.notional_usd / trade.entry_spot
        perp_size = spot_size  # delta-neutral

        if trade.direction == "premium":
            # spot LONG: close_spot - entry_spot
            spot_pnl = (close_spot - trade.entry_spot) * spot_size
            # perp SHORT: entry_perp - close_perp
            perp_pnl = (trade.entry_perp - close_perp) * perp_size
        else:  # discount
            # spot SHORT (借币卖): entry_spot - close_spot
            spot_pnl = (trade.entry_spot - close_spot) * spot_size
            # perp LONG: close_perp - entry_perp
            perp_pnl = (close_perp - trade.entry_perp) * perp_size

        # 平仓 fee
        close_fees = trade.notional_usd * self._cfg.fee_rate * Decimal("2")
        total_fees = trade.fees_paid + close_fees
        realized = spot_pnl + perp_pnl - close_fees  # 开仓 fee 已计入 fees_paid

        trade.exit_time = snap.timestamp
        trade.exit_basis_pct = snap.basis_pct
        trade.exit_spot = close_spot
        trade.exit_perp = close_perp
        trade.exit_reason = exit_reason
        trade.fees_paid = total_fees
        trade.realized_pnl = realized

        self._closed.append(trade)
        self._open.pop(trade.symbol, None)
        self._realized_pnl += realized
        self._equity = self._cfg.initial_capital_usd + self._realized_pnl

    # ------------------------------------------------------------------
    # peak dropoff（同生产 _check_peak_dropoff）
    # ------------------------------------------------------------------

    def _update_peak_cache(self, snaps: list[BasisSnapshot], ts: datetime) -> None:
        window_min = self._cfg.peak_window_minutes
        if window_min <= 0:
            return
        cutoff = ts - timedelta(minutes=float(window_min))
        for snap in snaps:
            entries = self._peak_cache[snap.symbol]
            entries.append((ts, abs(snap.basis_pct)))
            # 淘汰过期 + 容量上限
            self._peak_cache[snap.symbol] = [
                (t, b) for t, b in entries[-200:] if t >= cutoff
            ]

    def _check_peak_dropoff(
        self, symbol: str, current_abs_basis: Decimal, ts: datetime,
    ) -> bool:
        window_min = self._cfg.peak_window_minutes
        dropoff = self._cfg.min_peak_dropoff_pct
        if window_min <= 0 or dropoff <= 0:
            return True
        entries = self._peak_cache.get(symbol, [])
        if not entries:
            return True   # 冷启动豁免
        cutoff = ts - timedelta(minutes=float(window_min))
        valid = [(t, b) for t, b in entries if t >= cutoff]
        if not valid:
            return True
        peak = max(b for _, b in valid)
        return (peak - current_abs_basis) >= dropoff

    # ------------------------------------------------------------------
    # 资金曲线
    # ------------------------------------------------------------------

    def _record_equity(
        self, ts: datetime, snaps_at_ts: list[BasisSnapshot],
    ) -> None:
        # 计算未实现盈亏（用当前 ts 价格）
        snaps_by_sym = {s.symbol: s for s in snaps_at_ts}
        unrealized = _ZERO
        for sym, trade in self._open.items():
            snap = snaps_by_sym.get(sym)
            if snap is None:
                continue
            slip = self._cfg.slippage_pct / Decimal("100")
            if trade.direction == "premium":
                cs = snap.spot_price * (Decimal("1") - slip)
                cp = snap.perp_price * (Decimal("1") + slip)
            else:
                cs = snap.spot_price * (Decimal("1") + slip)
                cp = snap.perp_price * (Decimal("1") - slip)
            spot_size = (
                trade.notional_usd / trade.entry_spot if trade.entry_spot > 0 else _ZERO
            )
            if trade.direction == "premium":
                spot_pnl = (cs - trade.entry_spot) * spot_size
                perp_pnl = (trade.entry_perp - cp) * spot_size
            else:
                spot_pnl = (trade.entry_spot - cs) * spot_size
                perp_pnl = (cp - trade.entry_perp) * spot_size
            close_fees = trade.notional_usd * self._cfg.fee_rate * Decimal("2")
            unrealized += spot_pnl + perp_pnl - close_fees

        self._equity_curve.append(
            SpotPerpEquityPoint(
                timestamp=ts,
                equity_usd=self._cfg.initial_capital_usd + self._realized_pnl + unrealized,
                open_positions=len(self._open),
                total_realized_pnl=self._realized_pnl,
                total_unrealized_pnl=unrealized,
            )
        )
