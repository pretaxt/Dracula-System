#!/usr/bin/env python3
"""
dgr_btc 每日 mirror divergence 检查脚本
============================================
P11 paper trade 14 天验证期工具。

机制 (P9 验收门延伸到生产):
  1. 加载 yesterday-end paper state 快照 (dgr_btc_paper_state.json 24h 前)
  2. 拉 binance BTCUSDT 1h bars 过去 24h
  3. 用 MartingaleBacktestRunner 从 yesterday-end state + 今天 bars → 预测今日 paper state
  4. 比对实际今日 paper state (从 current state file 读)
  5. 若 divergence > 10% → telegram 告警 + 写入 audit log

调度: 每天 24:00 UTC 跑一次 (crontab)
  0 0 * * * cd /app && python scripts/dgr_btc_mirror_check.py

依赖文件:
  /app/state/dgr_btc_paper_state.json (paper session 实时状态)
  /app/state/dgr_btc_mirror_snapshots/  (本脚本维护的每日 snapshot)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

# 让脚本能 from app.* import (需在 dracula container 内跑或 sys.path 加 /app)
sys.path.insert(0, "/app")

from app.strategies.dgr_btc.backtest_runner import MartingaleBacktestRunner  # noqa: E402
from app.strategies.dgr_btc.engine import (  # noqa: E402
    EngineConfig,
    Layer,
    MartingaleEngine,
    StrategyState,
)


logger = logging.getLogger("dgr_btc_mirror_check")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


# ─── 配置 ───

STATE_PAPER = Path("/app/state/dgr_btc_paper_state.json")
SNAPSHOT_DIR = Path("/app/state/dgr_btc_mirror_snapshots")
AUDIT_LOG = Path("/app/state/dgr_btc_mirror_audit.jsonl")
DIVERGENCE_THRESHOLD_PCT = Decimal("10.0")  # >10% → 告警

WEIGHTS = [
    Decimal("0.0760"),
    Decimal("0.1141"),
    Decimal("0.1711"),
    Decimal("0.2566"),
    Decimal("0.3822"),
]


def _engine_config() -> EngineConfig:
    """从 yaml 读 martingale 参数；fallback 到金标默认"""
    import yaml
    yaml_path = Path("/app/config/strategies/dgr_btc_main.yaml")
    try:
        cfg = yaml.safe_load(yaml_path.read_text())
        mart = cfg.get("martingale", {})
        cap = cfg.get("capital", {})
        exec_cfg = cfg.get("execution", {})
    except Exception:
        mart, cap, exec_cfg = {}, {}, {}

    return EngineConfig(
        initial_capital=Decimal(str(cap.get("total_usdt", "10000"))),
        grid_step=Decimal(str(mart.get("grid_step", "0.05"))),
        factor=Decimal(str(mart.get("factor", "1.5"))),
        max_layers=int(mart.get("max_layers", 5)),
        tp_pct=Decimal(str(mart.get("tp_pct", "0.05"))),
        sl_pct=Decimal(str(mart.get("sl_pct", "0.10"))),
        layer_weights=[Decimal(str(w)) for w in mart.get("layer_weights", WEIGHTS)],
        fee_pct=Decimal(str(exec_cfg.get("fee_pct", "0.0006"))),
    )


# ─── State 加载 / 保存 ───


@dataclass
class StateSnapshot:
    ts: str
    cycle_id: int
    n_layers: int
    avg_cost: str
    total_qty: str
    cash: str
    n_tp: int
    n_sl: int
    realized_pnl: str

    @classmethod
    def from_state(cls, state: StrategyState, cash: Decimal) -> "StateSnapshot":
        return cls(
            ts=datetime.now(timezone.utc).isoformat(),
            cycle_id=state.cycle_id,
            n_layers=state.n_layers,
            avg_cost=str(state.avg_cost),
            total_qty=str(state.total_qty),
            cash=str(cash),
            n_tp=state.n_tp,
            n_sl=state.n_sl,
            realized_pnl=str(state.realized_pnl_usdt),
        )


def load_paper_state() -> tuple[StrategyState, Decimal]:
    """从 paper_state.json 加载当前 state + cash"""
    if not STATE_PAPER.exists():
        raise FileNotFoundError(f"paper state file missing: {STATE_PAPER}")
    data = json.loads(STATE_PAPER.read_text())
    if data.get("version") != "v2-martingale":
        raise ValueError(f"state version mismatch: {data.get('version')}")
    state = StrategyState(
        cycle_id=int(data.get("cycle_id", 0)),
        n_tp=int(data.get("n_tp", 0)),
        n_sl=int(data.get("n_sl", 0)),
        realized_pnl_usdt=Decimal(str(data.get("realized_pnl_usdt", "0"))),
        next_buy_price=Decimal(str(data["next_buy_price"])) if data.get("next_buy_price") else None,
        layers=[
            Layer(
                entry_price=Decimal(str(l["entry_price"])),
                qty_btc=Decimal(str(l["qty_btc"])),
                cost_usdt=Decimal(str(l["cost_usdt"])),
            )
            for l in data.get("layers", [])
        ],
    )
    cash = Decimal(str(data.get("cash_usdt", "10000")))
    return state, cash


def save_snapshot(snap: StateSnapshot, date: str) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"snap_{date}.json"
    path.write_text(json.dumps(asdict(snap), indent=2))


def load_snapshot(date: str) -> Optional[StateSnapshot]:
    path = SNAPSHOT_DIR / f"snap_{date}.json"
    if not path.exists():
        return None
    return StateSnapshot(**json.loads(path.read_text()))


# ─── Binance 拉 bars ───


async def fetch_24h_bars(symbol: str = "BTC/USDT") -> "pandas.DataFrame":
    """用 ccxt 拉过去 24h binance 1h bars"""
    import pandas as pd
    import ccxt.async_support as ccxt
    ex = ccxt.binance()
    try:
        since = int((datetime.now(timezone.utc) - timedelta(hours=25)).timestamp() * 1000)
        ohlcv = await ex.fetch_ohlcv(symbol, "1h", since=since, limit=24)
    finally:
        await ex.close()
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


# ─── 主流程 ───


def reconstruct_state_from_snapshot(snap: StateSnapshot, engine_cfg: EngineConfig) -> tuple[StrategyState, Decimal]:
    """从昨天 snapshot 重建 state (没有完整 layers, 只能粗略恢复)。

    注: snapshot 只保留 metrics, 不保留 layers 内部结构。
    所以 mirror check 的精确度仅到 cycle/cash/realized_pnl 维度，
    不到 in-flight layer 精度。这对生产环境足够。
    """
    state = StrategyState(
        cycle_id=snap.cycle_id,
        n_tp=snap.n_tp,
        n_sl=snap.n_sl,
        realized_pnl_usdt=Decimal(snap.realized_pnl),
    )
    # 重建 layers (粗略，单层近似)
    total_qty = Decimal(snap.total_qty)
    avg_cost = Decimal(snap.avg_cost)
    if total_qty > 0 and avg_cost > 0:
        # 简化: 把整个仓位当一个虚拟 layer (snapshot 保留的是聚合)
        state.layers = [Layer(
            entry_price=avg_cost,
            qty_btc=total_qty,
            cost_usdt=total_qty * avg_cost,
        )]
        state.next_buy_price = avg_cost * (Decimal("1") - engine_cfg.grid_step)
    cash = Decimal(snap.cash)
    return state, cash


def compare_states(
    expected: StateSnapshot, actual: StateSnapshot, threshold_pct: Decimal
) -> tuple[bool, list[str]]:
    """对比预期 vs 实际 snapshot，返回 (is_diverged, [diff_msgs])"""
    diffs: list[str] = []

    def pct_diff(a: Decimal, b: Decimal) -> Decimal:
        if a == 0:
            return Decimal("100") if b != 0 else Decimal("0")
        return abs((b - a) / a) * Decimal("100")

    # cycle_id, n_tp, n_sl: 整数差异
    if expected.cycle_id != actual.cycle_id:
        diffs.append(f"周期 ID 不一致 预期={expected.cycle_id} 实际={actual.cycle_id}")
    if abs(expected.n_tp - actual.n_tp) > 0:
        diffs.append(f"止盈次数不一致 预期={expected.n_tp} 实际={actual.n_tp}")
    if abs(expected.n_sl - actual.n_sl) > 0:
        diffs.append(f"止损次数不一致 预期={expected.n_sl} 实际={actual.n_sl}")

    # cash: % 差异
    exp_cash = Decimal(expected.cash)
    act_cash = Decimal(actual.cash)
    cash_pct = pct_diff(exp_cash, act_cash)
    if cash_pct > threshold_pct:
        diffs.append(f"现金偏差 {cash_pct:.2f}% 预期=${exp_cash:.2f} 实际=${act_cash:.2f}")

    # realized_pnl: 绝对 USDT 差异 (容差 $5 或 10% 取大)
    exp_pnl = Decimal(expected.realized_pnl)
    act_pnl = Decimal(actual.realized_pnl)
    pnl_diff_abs = abs(act_pnl - exp_pnl)
    if pnl_diff_abs > Decimal("5") and pct_diff(exp_pnl, act_pnl) > threshold_pct:
        diffs.append(f"已实现盈亏偏差 ${pnl_diff_abs:.2f} 预期=${exp_pnl:.2f} 实际=${act_pnl:.2f}")

    return (len(diffs) > 0, diffs)


def write_audit(payload: dict) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    payload["ts"] = datetime.now(timezone.utc).isoformat()
    with open(AUDIT_LOG, "a") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def send_telegram_alert(msg: str) -> None:
    """复用 dracula 现有 telegram bot (env var TELEGRAM_BOT_TOKEN + CHAT_ID)"""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("telegram 环境变量未设置, 跳过推送")
        return
    import httpx
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            await client.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"})
            logger.info("telegram 告警已发送")
        except Exception as e:
            logger.exception(f"telegram send failed: {e}")


async def main() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()

    logger.info(f"镜像检查: 今日={today} 昨日={yesterday}")

    # 加载当前 paper state
    try:
        cur_state, cur_cash = load_paper_state()
    except FileNotFoundError:
        logger.warning("paper 状态文件未找到 (session 未启动?)")
        return 1
    actual_snap = StateSnapshot.from_state(cur_state, cur_cash)
    save_snapshot(actual_snap, today)

    # 加载昨天 snapshot
    y_snap = load_snapshot(yesterday)
    if y_snap is None:
        logger.info(f"昨日 ({yesterday}) 无快照 — 首跑，仅保存当日基线")
        write_audit({"event": "baseline_saved", "date": today, "snapshot": asdict(actual_snap)})
        return 0

    # 用昨天 snapshot 重建 state + 喂今天 bars
    cfg = _engine_config()
    y_state, y_cash = reconstruct_state_from_snapshot(y_snap, cfg)

    df = await fetch_24h_bars()
    logger.info(f"已拉取最近 24h 共 {len(df)} 根 1h K 线")

    # P3 修复 (Codex medium): 用 init_state + init_cash 直接续跑昨日 state,
    # 不再丢弃重建出来的 y_state. layers/avg_cost/next_buy/n_tp/n_sl/realized_pnl
    # 完整延续, mirror 检验对比真正的"延续场景"行为.
    runner_replay = MartingaleBacktestRunner(cfg)
    result = runner_replay.run(df, init_state=y_state, init_cash=y_cash)

    expected_snap = StateSnapshot(
        ts=actual_snap.ts,
        cycle_id=y_snap.cycle_id + result.n_cycles,
        n_layers=0,  # 终态不一定保留 layers，简化
        avg_cost="0",
        total_qty="0",
        cash=str(result.final_equity),
        n_tp=y_snap.n_tp + result.n_tp,
        n_sl=y_snap.n_sl + result.n_sl,
        realized_pnl=str(Decimal(y_snap.realized_pnl) + (result.final_equity - y_cash)),
    )

    # 对比
    diverged, diffs = compare_states(expected_snap, actual_snap, DIVERGENCE_THRESHOLD_PCT)
    audit_payload = {
        "event": "mirror_check",
        "date": today,
        "diverged": diverged,
        "diffs": diffs,
        "expected": asdict(expected_snap),
        "actual": asdict(actual_snap),
    }
    write_audit(audit_payload)

    if diverged:
        # 拉当前 BTC 价用于浮动盈亏估算 (用 24h bars 最后一根 close 作为现价)
        try:
            cur_price_f = float(df.iloc[-1]["close"]) if df is not None and len(df) > 0 else 0.0
        except Exception:
            cur_price_f = 0.0

        def _fmt_pos(snap: StateSnapshot, label: str) -> str:
            """单边持仓人话化: BTC 数量 / 平均成本 / 当前价 / 浮动盈亏"""
            try:
                qty = float(Decimal(snap.total_qty))
                avg = float(Decimal(snap.avg_cost))
                cash = float(Decimal(snap.cash))
                realized = float(Decimal(snap.realized_pnl))
            except Exception:
                qty, avg, cash, realized = 0.0, 0.0, 0.0, 0.0
            notional = qty * cur_price_f if cur_price_f > 0 else qty * avg
            unrealized = (cur_price_f - avg) * qty if cur_price_f > 0 and avg > 0 else 0.0
            unrealized_pct = (cur_price_f / avg - 1) * 100 if cur_price_f > 0 and avg > 0 else 0.0
            equity = cash + notional
            lines = [
                f"<b>{label}</b>:",
                f"  周期 {snap.cycle_id} · 止盈 {snap.n_tp} · 止损 {snap.n_sl} · 持仓层数 {snap.n_layers}",
            ]
            if qty > 0:
                lines.append(f"  持仓 {qty:.5f} BTC · 平均成本 ${avg:,.2f}")
                if cur_price_f > 0:
                    lines.append(f"  按现价 ${cur_price_f:,.2f} → 浮动盈亏 ${unrealized:+,.2f} ({unrealized_pct:+.2f}%)")
                lines.append(f"  现金 ${cash:,.2f} · 持仓估值 ${notional:,.2f} · 总权益 ${equity:,.2f}")
            else:
                lines.append(f"  空仓 · 现金 ${cash:,.2f}")
            if realized != 0:
                lines.append(f"  累计已实现盈亏 ${realized:+,.2f}")
            return "\n".join(lines)

        msg = (
            f"⚠️ <b>dgr_btc 镜像一致性告警</b> ({today})\n"
            f"<i>每日凌晨用回测引擎重放过去 24h, 对比实盘 paper 当前状态</i>\n\n"
            f"{_fmt_pos(expected_snap, '预期 (回测复盘)')}\n\n"
            f"{_fmt_pos(actual_snap, '实际 (paper 实盘)')}\n\n"
            f"<b>偏差项</b>:\n" + "\n".join(f"  · {d}" for d in diffs)
        )
        logger.warning(f"镜像偏差: {diffs}")
        await send_telegram_alert(msg)
        return 2

    logger.info(f"镜像一致: paper ≈ 回测 (周期={actual_snap.cycle_id} 止盈={actual_snap.n_tp} 止损={actual_snap.n_sl})")
    return 0


if __name__ == "__main__":
    rc = asyncio.run(main())
    sys.exit(rc)
