# dgr_btc v2 Revamp — 部署 Patch 集合 (P7 + P8 准备)

**生成于**: 2026-05-26
**适用**: feature/dgr_btc_v2_revamp 分支部署时 (P10) 在 host /opt/dracula 上手工对接

## 为什么走 patch 而非 commit

本地 GitHub clone main 的以下文件**不含 dgr_btc 集成段**（host 上 long-running uncommitted 工作）:
- `frontend/lib/strategies/catalog.ts`（本地 263 行 / host 356 行）
- `backend/app/notifications/telegram_bot_handlers.py`（本地 404 行 / host 466 行）
- `backend/app/main.py`（无 dgr_btc startup）
- `backend/app/api/v1/strategies.py / positions.py / orders.py / dashboard.py / backtest.py`（无 dgr_btc 注入）
- `backend/app/services/runtime_overrides.py`（无 dgr_btc 子节）

直接在 feature 分支修改会把 host 长期工作隐含 commit 上去 → 部署 merge 时和 host working tree 大冲突。

更稳的做法是：feature 分支只含 dgr_btc 模块本身的改造（已完成 P3/P4/P5/P6.1/P9），caller 集成段在 P10 部署时按本文档 patch。

---

## P7-A. Frontend catalog.ts — 替换 dgr_btc 卡片定义

Host 现有 ~line 316-340（v1 paired-hedge 描述）替换为：

```typescript
{
  num: '13', id: 'dgr-btc', zhName: 'Martingale + 再定心',
  enLabel: 'MARTINGALE + RECENTER · Paper', phase: 'P0', status: 'PAPER',
  capital: '$10,000', monthly: null, positions: '—', posLabel: 'layers',
  desc: '单边做多 BTC + 下跌马丁加仓 + 平均成本止盈/止损. 跨 6 窗口验证 4/5 正夏普 + 5/5 胜 B&H. W3 2022 全年大熊 -5% vs B&H -64%.',
  monthlyTone: 'positive',
  thesis: '价格下跌时按 [7.6/11.4/17.1/25.6/38.2]% 权重加仓拉低成本, 每加一档后 next_buy = avg_cost × 0.95 再定心. 平均成本 +5% 止盈清仓, -10% 止损切断深熊死亡螺旋. 不依赖方向预测, 靠 cycle 频率 + 损益结构吃 BTC 震荡。',
  risks: [
    '暴跌窗口策略仍亏 (LUNA 期 -25%), 但比 B&H 少亏一半',
    '大幅单边下跌 + 后续无反弹 → SL 触发, 真实亏损固化',
    '马丁本身是著名炸账户结构, 全靠 SL 兜底 — SL 参数选择 ±2% 区间结果差异大',
  ],
  rules: {
    entry: [
      '空仓 → 立即建 layer 1 (initial × 7.6% = $760)',
      '价格跌至 avg_cost × 0.95 → 加下一档 (按递增权重)',
      '加层后 next_buy 重新计算 = 新 avg_cost × 0.95 (再定心)',
    ],
    exit: [
      'price ≥ avg_cost × 1.05 → 全平止盈, 开新 cycle',
      'low ≤ avg_cost × 0.90 → 强制止损全平',
      '满 5 层后仍跌 → 等待 SL 触发 (max_forced_holds_hours=24)',
    ],
    risk: [
      '单日 -5% → 自动 KILL',
      '总回撤 > 30% → 自动 KILL',
      '满层卡死 > 24h → 自动 KILL (LUNA 锁死防护)',
    ],
  },
}
```

---

## P7-B. Frontend strategy detail page

文件: `frontend/app/(dashboard)/strategies/[id]/page.tsx`

需要改的字段表单 (PATCH /dgr-btc/config 用)：

**删除字段** (paired_inverse 专属):
- delta.upper_limit / lower_limit / rebalance_threshold
- grid.step_usdt / qty_per_grid / dynamic_bounds.* / recenter_trigger_pct
- position_limits.max_short_btc / leverage

**新增字段** (Martingale):
- martingale.grid_step (default 0.05)
- martingale.factor (default 1.5)
- martingale.max_layers (default 5)
- martingale.tp_pct (default 0.05)
- martingale.sl_pct (default 0.10)
- execution.fee_pct (default 0.0006)
- risk.daily_loss_cap_pct / max_forced_holds_hours / max_drawdown_pct

---

## P8. Telegram handler — `_read_dgr_btc_open_rows`

文件: `backend/app/notifications/telegram_bot_handlers.py` 当前 ~197-260 行

替换为单腿版本：

```python
def _read_dgr_btc_open_rows(app_state) -> tuple[int, list[str]]:
    """从 app.state.dgr_btc_paper.snapshot() 读单边马丁仓位.

    v2 revamp: 单边 long-only, perp 永远 0.
    展示 layers / avg_cost / next_buy / cycle_id / unrealized_pnl.
    """
    sess = getattr(app_state, "dgr_btc_paper", None)
    if sess is None or not getattr(sess, "_running", False):
        return 0, []
    try:
        snap = sess.snapshot()
    except Exception:
        logger.exception("dgr_btc_telegram_snapshot_failed")
        return 0, []

    n_layers = int(snap.get("n_layers", 0))
    if n_layers == 0:
        # 空仓 cycle，依然显示一行表明 session 在跑
        return 1, [
            f"  <code>NO POSITION</code>  cycle_id={snap.get('cycle_id', 0)}  "
            f"mode={'LIVE' if sess.live_mode else 'PAPER'}",
        ]

    spot = snap.get("positions", {}).get("spot", {})
    qty = spot.get("qty", "0")
    avg_entry = spot.get("avg_entry", "0")
    unrealized = spot.get("unrealized_pnl", "0")
    spot_price = snap.get("spot_price", "0")
    next_buy = snap.get("next_buy_price") or "—"
    cycle_id = snap.get("cycle_id", 0)
    n_tp = snap.get("n_tp", 0)
    n_sl = snap.get("n_sl", 0)
    mode = "LIVE" if sess.live_mode else "PAPER"

    lines = [
        f"  <code>BTC LONG </code>  qty={qty} avg=${avg_entry}",
        f"  <code>layers   </code>  {n_layers}/5  next_buy=${next_buy}",
        f"  <code>cycle    </code>  #{cycle_id}  TP={n_tp} SL={n_sl}",
        f"  <code>uPnL     </code>  ${unrealized}  spot=${spot_price}  mode={mode}",
    ]
    return n_layers, lines
```

主调用处 ~line 172 修改：

```python
dgr_count, dgr_lines = _read_dgr_btc_open_rows(app_state)
if dgr_count > 0:
    out.append(f"<b>—— #13 Martingale dgr_btc ({dgr_count} layers) ——</b>")
    out.extend(dgr_lines)
```

---

## P10 部署时操作顺序

1. ssh dracula
2. cd /opt/dracula
3. git fetch origin feature/dgr_btc_v2_revamp
4. **不要直接 merge** —— host 有 working tree 修改
5. 备份 host 现有未 commit 工作（cp -r 关键文件）
6. **手工**应用本文档 P7-A / P7-B / P8 patch 到 host 现有文件
7. **rsync** feature 分支的 dgr_btc 模块 + tests + yaml 覆盖 host
   - `backend/app/strategies/dgr_btc/` （含 engine.py / backtest_runner.py / 新 paper_trading.py）
   - `backend/tests/strategies/dgr_btc/` （含 4 个新测试套件）
   - `config/strategies/dgr_btc_main.yaml`
8. archive host 旧 state files (audit clause L):
   - `/app/state/dgr_btc_paper_state.json` → `.bak_v1_20260526`
   - `/app/state/dgr_btc_audit.jsonl` → `.bak_v1_20260526`
   - `/app/state/dgr_btc_trades.jsonl` → `.bak_v1_20260526`
9. **创建 KILL 文件** 默认 ON:
   - `touch /app/state/dgr_btc_KILL`
10. 数据库 strategies 表 row PNL reset:
    - `UPDATE strategies SET realized_pnl_usdt=0, unrealized_pnl_usdt=0, trade_count=0 WHERE instance_name = 'dgr_btc_main'`
11. docker compose restart (paper mode + KILL ON)
12. 监控 logs 确认 "dgr_btc_paper_start" 出现且不交易（KILL 拦截）
13. user PATCH `/strategies/dgr-btc/config` 显式删除 KILL 文件后才开始 paper trade

## 不变量

- v1 paired-hedge 模块永远不再 boot（删除/未引用）
- 部署后默认 PAPER + KILL ON，不会自动交易
- 14 天后基于实测决定 LIVE / NO-GO
