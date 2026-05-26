#!/usr/bin/env python3
"""
dgr_btc_seed_live_position.py
==============================
LIVE 阶段 1 切换辅助脚本：用户在 binance 手动建仓后, 跑这个脚本一次性把
positions 表 id='dgr-btc-main-singleton' 这行种好, 让下次 restart 时
dgr_btc paper_trading._try_restore_state 接管.

用法 (在 dracula 上 docker exec 进 api 容器跑):
    sudo docker exec -it dracula-api-1 python3 /app/scripts/dgr_btc_seed_live_position.py \
        --spot-qty 0.10 \
        --spot-fill-price 77013.50 \
        --perp-qty 0.10 \
        --perp-fill-price 76985.20 \
        --capital-total 15000 \
        --grid-center 77000 \
        --grid-step 500 \
        --width-pct 0.15 \
        [--dry-run]

任选 --dry-run 先看 INSERT 的内容不真写库.

强约束 (脚本拒跑):
    - 已存在 dgr-btc-main-singleton 且 status=OPEN  → exit 1 (避免覆盖已有 state)
    - spot_qty / perp_qty 任一 <= 0                → exit 1
    - capital_total 不够减 spot 成本 (cash 会为负) → exit 1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid as _uuid_mod
from datetime import datetime, timezone
from decimal import Decimal

# Deterministic stable UUID for dgr_btc_main singleton position.
# = uuid5(NAMESPACE_URL, "dgr-btc-main-singleton")
# = 57fa1a5e-f77b-57e8-92c4-73cd1592a5d7  (do NOT change without migration)
DGR_BTC_SINGLETON_UUID = str(_uuid_mod.uuid5(_uuid_mod.NAMESPACE_URL, "dgr-btc-main-singleton"))


async def main() -> int:
    ap = argparse.ArgumentParser(description="Seed dgr_btc LIVE position into DB")
    ap.add_argument("--spot-qty", type=Decimal, required=True,
                    help="binance 手动买入的 spot BTC qty (e.g. 0.10)")
    ap.add_argument("--spot-fill-price", type=Decimal, required=True,
                    help="实际 spot 成交均价 (USDT)")
    ap.add_argument("--perp-qty", type=Decimal, required=True,
                    help="binance 手动开 short 的 perp BTC qty 绝对值 (e.g. 0.10)")
    ap.add_argument("--perp-fill-price", type=Decimal, required=True,
                    help="实际 perp short 成交均价 (USDT)")
    ap.add_argument("--capital-total", type=Decimal, required=True,
                    help="初始 capital USDT (e.g. 15000), cash = capital - spot_qty*spot_fill")
    ap.add_argument("--grid-center", type=Decimal, required=True,
                    help="grid center 价 (一般取 spot_fill 整数化)")
    ap.add_argument("--grid-step", type=Decimal, default=Decimal("500"),
                    help="grid 间距 USD (default 500)")
    ap.add_argument("--width-pct", type=Decimal, default=Decimal("0.15"),
                    help="dynamic bounds width pct (default 0.15)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印不写 DB")
    args = ap.parse_args()

    # --- 校验 ---
    if args.spot_qty <= 0 or args.perp_qty <= 0:
        print("ERROR: spot-qty / perp-qty 必须 > 0", file=sys.stderr)
        return 1

    spot_cost = args.spot_qty * args.spot_fill_price
    cash = args.capital_total - spot_cost
    if cash < 0:
        print(f"ERROR: capital ${args.capital_total} 不够付 spot 成本 ${spot_cost} (cash 会为 ${cash})",
              file=sys.stderr)
        return 1

    grid_lower = args.grid_center * (Decimal("1") - args.width_pct)
    grid_upper = args.grid_center * (Decimal("1") + args.width_pct)
    delta_btc = args.spot_qty - args.perp_qty
    notional = spot_cost + args.perp_qty * args.perp_fill_price

    notes_dict = {
        "center": str(args.grid_center),
        "n_recenters": 0,
        "last_recenter_ts": None,
        "grid_lower": str(grid_lower),
        "grid_upper": str(grid_upper),
        "cash_usdt": str(cash),
    }
    notes_json = json.dumps(notes_dict, ensure_ascii=False)

    print("=" * 70)
    print("dgr_btc LIVE seed — 将要写入 DB 的内容:")
    print("=" * 70)
    print(f"  position id (UUID) : {DGR_BTC_SINGLETON_UUID}")
    print(f"  position label     : dgr-btc-main-singleton (= uuid5 source name)")
    print(f"  strategy_instance  : dgr_btc_main")
    print(f"  symbol             : BTC/USDT")
    print(f"  status             : OPEN")
    print(f"  notional_usd       : ${notional}")
    print(f"  ── spot leg ── ")
    print(f"     size            : {args.spot_qty} BTC")
    print(f"     entry_price     : ${args.spot_fill_price}")
    print(f"     side            : BUY (long)")
    print(f"     leverage        : 1")
    print(f"  ── perp leg ── ")
    print(f"     size            : {args.perp_qty} BTC")
    print(f"     entry_price     : ${args.perp_fill_price}")
    print(f"     side            : SELL (short)")
    print(f"     leverage        : 10")
    print(f"  notes (JSON):")
    print(f"     {notes_json}")
    print()
    print(f"  → 启动后 strategy 视图: spot {args.spot_qty} BTC, perp -{args.perp_qty} BTC")
    print(f"  → delta = {delta_btc:+.4f} BTC  (target 0)")
    print(f"  → cash_remaining = ${cash}")
    print()

    if args.dry_run:
        print("DRY-RUN: 不写 DB. 去掉 --dry-run 实际执行.")
        return 0

    # --- 写 DB ---
    # 这里复用 paper_trading._persist_position_to_db 的 PositionManager.save() 路径
    from app.risk.position_manager import PositionManager
    from app.risk.models import (
        ExitReason, Position as DomainPosition, PositionLeg, PositionStatus,
    )
    from app.exchanges.models import InstrumentType, Side as DomainSide, Symbol

    pm = PositionManager(strategy_type="dgr_btc")

    # 检查是否已存在
    n_loaded = await pm.load_open_positions()
    for p in pm._positions.values() if hasattr(pm, "_positions") else []:
        if p.strategy_instance == "dgr_btc_main" and p.status == PositionStatus.OPEN:
            print(f"ERROR: 已存在 dgr_btc_main open position id={p.id}, 拒绝覆盖. ", file=sys.stderr)
            print(f"       若要覆盖, 先手动 UPDATE positions SET status='closed' WHERE id='{p.id}'.",
                  file=sys.stderr)
            return 1

    sym = Symbol("BTC", "USDT")
    spot_leg = PositionLeg(
        exchange="binance",
        symbol=sym,
        instrument_type=InstrumentType.SPOT,
        side=DomainSide.BUY,
        size=args.spot_qty,
        entry_price=args.spot_fill_price,
        leverage=Decimal("1"),
    )
    perp_leg = PositionLeg(
        exchange="binance",
        symbol=sym,
        instrument_type=InstrumentType.PERPETUAL,
        side=DomainSide.SELL,
        size=args.perp_qty,
        entry_price=args.perp_fill_price,
        leverage=Decimal("10"),
    )
    pos = DomainPosition(
        id=DGR_BTC_SINGLETON_UUID,
        strategy_instance="dgr_btc_main",
        symbol=sym,
        notional_usd=notional,
        legs=[spot_leg, perp_leg],
        status=PositionStatus.OPEN,
        realized_pnl=Decimal("0"),
        funding_received=Decimal("0"),
        fees_paid=Decimal("0"),
        opened_at=datetime.now(timezone.utc),
        notes=notes_json,
    )
    try:
        await pm.save(pos)
        print("OK: dgr-btc-main-singleton 已写入 DB.")
        print()
        print("下一步:")
        print("  1. 改 yaml: live_mode=true + live_safety.enabled=true + dry_run=true")
        print("  2. sudo docker restart dracula-api-1")
        print("  3. 看 startup log: 应有 'dgr_btc_restored_from_db center=... spot_qty=... perp_qty=...'")
        return 0
    except Exception as e:
        print(f"ERROR: DB 写入失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
