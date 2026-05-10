"""#02 跨所 funding 差套利回测引擎

按 funding 结算时间推进。每次结算：
  1. 对每个 symbol 收集所有 exchange 的当期 funding rate
  2. 计算所有 (long, short) 配对的 diff_apr，> min_diff_apr_pct 触发开仓
  3. 已持仓累计本期 funding 差（short_rate × notional - long_rate × notional）
  4. 退出条件：max_hold / diff_apr 衰减到 exit_diff_apr_pct / 强制收敛

PnL 简化模型（仅算 funding + fees，忽略 perp 价格漂移因为是跨所对冲）：
  trade_pnl = sum(period_funding_diff × notional) - 2 × open_fee - 2 × close_fee
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from app.backtest.perp_basis_models import (
    CrossExchangeOpportunity,
    PerpBasisBacktestConfig,
    PerpBasisBacktestResult,
    PerpBasisEquityPoint,
    PerpBasisTrade,
    PerpFundingSnapshot,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def run_perp_basis_backtest(
    snapshots: Iterable[PerpFundingSnapshot],
    config: PerpBasisBacktestConfig,
) -> PerpBasisBacktestResult:
    """按时间推进的事件驱动回测。

    snapshots 必须按 timestamp 升序排列。
    """
    snaps = list(snapshots)
    if not snaps:
        return PerpBasisBacktestResult(
            config=config,
            final_equity_usd=config.initial_capital_usd,
        )
    snaps.sort(key=lambda s: s.timestamp)

    # 分组：timestamp → list of snapshots
    by_ts: dict[datetime, list[PerpFundingSnapshot]] = defaultdict(list)
    for s in snaps:
        by_ts[s.timestamp].append(s)

    open_trades: list[PerpBasisTrade] = []
    closed_trades: list[PerpBasisTrade] = []
    equity = config.initial_capital_usd
    equity_curve: list[PerpBasisEquityPoint] = []

    for ts in sorted(by_ts.keys()):
        period_snaps = by_ts[ts]
        # symbol → exchange → snap
        by_sym: dict[str, dict[str, PerpFundingSnapshot]] = defaultdict(dict)
        for s in period_snaps:
            by_sym[s.symbol][s.exchange] = s

        # 1. 已持仓累计本期 funding（基于本时刻 short/long exchange 的 rate）
        for trade in open_trades:
            ex_long = by_sym.get(trade.symbol, {}).get(trade.long_exchange)
            ex_short = by_sym.get(trade.symbol, {}).get(trade.short_exchange)
            if ex_short and ex_long:
                # short 收 rate，long 付 rate；diff = short_rate - long_rate
                diff_rate = ex_short.funding_rate - ex_long.funding_rate
                period_funding = diff_rate * trade.notional_usd
                trade.funding_collected += period_funding

        # 2. 退出检查
        still_open: list[PerpBasisTrade] = []
        for trade in open_trades:
            held = (ts - trade.open_at).total_seconds() / 3600
            held_dec = Decimal(str(held))
            should_close = False
            reason = None
            # max_hold
            if held_dec >= config.max_hold_hours:
                should_close = True
                reason = "max_hold"
            elif held_dec >= config.min_hold_hours:
                # diff_apr 衰减检查
                ex_long = by_sym.get(trade.symbol, {}).get(trade.long_exchange)
                ex_short = by_sym.get(trade.symbol, {}).get(trade.short_exchange)
                if ex_short and ex_long:
                    cur_diff_apr = ex_short.apr_pct - ex_long.apr_pct
                    if cur_diff_apr <= config.exit_diff_apr_pct:
                        should_close = True
                        reason = "diff_decay"

            if should_close:
                ex_long = by_sym.get(trade.symbol, {}).get(trade.long_exchange)
                ex_short = by_sym.get(trade.symbol, {}).get(trade.short_exchange)
                long_exit = ex_long.perp_price if ex_long else trade.long_entry_price
                short_exit = ex_short.perp_price if ex_short else trade.short_entry_price
                trade.long_exit_price = long_exit
                trade.short_exit_price = short_exit
                trade.closed_at = ts
                trade.exit_reason = reason
                # close fees (single side, both legs)
                close_fees = trade.notional_usd * config.fee_rate * Decimal("2")
                trade.fees_paid += close_fees
                # realized pnl = funding 累计 - 总 fees - 滑点
                # 价格漂移在跨所 short+long 抵消（近似）
                trade.realized_pnl = trade.funding_collected - trade.fees_paid
                equity += trade.realized_pnl
                closed_trades.append(trade)
            else:
                still_open.append(trade)
        open_trades = still_open

        # 3. 入场扫描 — 计算所有跨所配对
        if len(open_trades) < config.max_concurrent:
            opportunities = _scan_opportunities(by_sym, config)
            # 按 diff_apr 降序，挑最优
            opportunities.sort(key=lambda o: o.diff_apr_pct, reverse=True)
            for opp in opportunities:
                if len(open_trades) >= config.max_concurrent:
                    break
                # 已开仓相同 (symbol, long, short) 跳过
                already_open = any(
                    t.symbol == opp.symbol
                    and t.long_exchange == opp.long_exchange
                    and t.short_exchange == opp.short_exchange
                    for t in open_trades
                )
                if already_open:
                    continue
                # open fees
                open_fees = config.notional_per_position * config.fee_rate * Decimal("2")
                # 滑点（perp 单边）— 双腿 2 × slippage
                slippage_loss = (
                    config.notional_per_position
                    * (config.slippage_pct / _HUNDRED)
                    * Decimal("2")
                )
                trade = PerpBasisTrade(
                    symbol=opp.symbol,
                    long_exchange=opp.long_exchange,
                    short_exchange=opp.short_exchange,
                    open_at=ts,
                    notional_usd=config.notional_per_position,
                    long_entry_price=opp.long_perp_price,
                    short_entry_price=opp.short_perp_price,
                    entry_diff_apr_pct=opp.diff_apr_pct,
                    fees_paid=open_fees + slippage_loss,
                )
                open_trades.append(trade)

        # 4. equity snapshot
        unrealized = sum((t.funding_collected - t.fees_paid for t in open_trades), _ZERO)
        equity_curve.append(PerpBasisEquityPoint(
            timestamp=ts,
            equity_usd=equity + unrealized,
        ))

    # 强制平掉残留持仓（按最后已知价格 + diff_apr）
    last_ts = max(by_ts.keys())
    last_snaps = by_ts[last_ts]
    last_by_sym: dict[str, dict[str, PerpFundingSnapshot]] = defaultdict(dict)
    for s in last_snaps:
        last_by_sym[s.symbol][s.exchange] = s
    for trade in open_trades:
        ex_long = last_by_sym.get(trade.symbol, {}).get(trade.long_exchange)
        ex_short = last_by_sym.get(trade.symbol, {}).get(trade.short_exchange)
        trade.long_exit_price = ex_long.perp_price if ex_long else trade.long_entry_price
        trade.short_exit_price = ex_short.perp_price if ex_short else trade.short_entry_price
        trade.closed_at = last_ts
        trade.exit_reason = "force_close_eob"
        close_fees = trade.notional_usd * config.fee_rate * Decimal("2")
        trade.fees_paid += close_fees
        trade.realized_pnl = trade.funding_collected - trade.fees_paid
        equity += trade.realized_pnl
        closed_trades.append(trade)

    total_funding = sum((t.funding_collected for t in closed_trades), _ZERO)
    total_fees = sum((t.fees_paid for t in closed_trades), _ZERO)

    logger.info(
        "perp_basis_backtest_complete",
        trades=len(closed_trades),
        final_equity=str(round(equity, 2)),
        total_funding=str(round(total_funding, 2)),
        total_fees=str(round(total_fees, 2)),
    )
    return PerpBasisBacktestResult(
        config=config,
        trades=closed_trades,
        equity_curve=equity_curve,
        final_equity_usd=equity,
        total_funding_collected=total_funding,
        total_fees_paid=total_fees,
    )


def _scan_opportunities(
    by_sym: dict[str, dict[str, PerpFundingSnapshot]],
    config: PerpBasisBacktestConfig,
) -> list[CrossExchangeOpportunity]:
    """枚举所有 (symbol, long_ex, short_ex) 配对，过滤 min_diff_apr_pct。"""
    opps: list[CrossExchangeOpportunity] = []
    for sym, ex_map in by_sym.items():
        if len(ex_map) < 2:
            continue
        snaps = list(ex_map.values())
        # 过滤极端值（脏数据保护）
        snaps = [
            s for s in snaps
            if abs(s.apr_pct) <= config.max_abs_apr_pct
        ]
        # 枚举配对
        for i, a in enumerate(snaps):
            for b in snaps[i + 1:]:
                # diff = max - min
                if a.apr_pct >= b.apr_pct:
                    short_snap, long_snap = a, b
                else:
                    short_snap, long_snap = b, a
                diff_apr = short_snap.apr_pct - long_snap.apr_pct
                if diff_apr < config.min_diff_apr_pct:
                    continue
                opps.append(CrossExchangeOpportunity(
                    timestamp=short_snap.timestamp,
                    symbol=sym,
                    long_exchange=long_snap.exchange,
                    short_exchange=short_snap.exchange,
                    long_apr_pct=long_snap.apr_pct,
                    short_apr_pct=short_snap.apr_pct,
                    long_perp_price=long_snap.perp_price,
                    short_perp_price=short_snap.perp_price,
                ))
    return opps
