# Dracula 回测标准 (2026-05-27)

**强规则**：所有 dracula 策略回测必须 byte-equal 复刻真实交易（paper / LIVE）执行流。任何不一致都是 bug，禁止用"回测和实盘不同"作为借口。

## 第一原则：单一执行流

```
真实交易 (paper / LIVE):
  每 5s tick:
    1. fetch_klines("1m", limit=2) → 最新 1m bar (实时 OHLC)
    2. engine.decide(state, price=bar.close, low=bar.low)
    3. while Decision != NOOP: 处理多档 add 一次性, fill at decision.target_price × (1 ± fee)
    4. engine.apply_fill(state, decision, fill_price, qty, cost)
    5. persist state

回测:
  逐 1m bar iterator:
    1. engine.decide(state, price=bar.close, low=bar.low)  ← 完全相同函数
    2. while Decision != NOOP: 同样处理
    3. 同样 fill 公式
    4. 同样 apply_fill
```

**唯一允许差异**：数据源（CSV vs WS）+ 时间（历史 vs 实时）。**所有逻辑必须共享同一 engine.MartingaleEngine**。

## 标准参数（不可逆，写死）

| 参数 | 值 | 来源 |
|---|---|---|
| **K 线周期** | **1m** | 与 paper `fetch_klines("1m")` 完全一致 |
| **Low 数据** | **真实 bar.low**（intrabar） | binance 1m bar 的真实 OHLC |
| **Close 数据** | **bar.close** | 同上 |
| **单边费用** | **0.06%**（0.04% taker + 0.02% slippage） | binance 现货 retail |
| **决策频率** | **每 bar 一次完整 decide 循环**直到 NOOP | engine 内部 while 循环 |

## 禁止的"优化"

| ❌ 禁止 | 理由 |
|---|---|
| `bar_mode = close_only`（low=close） | paper 5s tick 看到真实 1m low，不是 close-only |
| `bar_interval = "1h"` | 1h aggregation 丢失时序信息，与 paper 行为不一致 |
| Maker rebate / fee 优化 | 现实成交是 taker（速度优先），fee 必须保守估 |
| Funding rate（单边版本） | 单边 long-only spot 不收 funding，写 0 |
| 跳过满层后的 forced_holds | 满层不出场是真实情形，回测必须如实模拟 |
| 滑点假设为 0 | 真实成交有 slippage，0.02% 是保守估 |

## 三套验证标准（任一失败 = 回测无效）

### 验证 1: Engine 单测（19 项）
```
test_engine.py:
  - ENTRY layer 0
  - 加层 + 再定心
  - TP / SL / NOOP
  - 配置校验
  - Fill 公式与跨窗口脚本 byte-equal
```

### 验证 2: 跨窗口一致性（25 项）
```
test_backtest_consistency.py:
  - W7 / W1-W5 跨 6 个 BTC 历史窗口
  - 1m bar 真实 OHLC
  - 容差：±0.5pp 总收益 / ±1 TP/SL 计数 / ±1pp MaxDD
```

### 验证 3: Mirror 等价性（7 项）
```
test_mirror_equivalence.py:
  - paper_trading._process_decisions(close, low)
  - vs MartingaleBacktestRunner.run(df)
  - 同样 (close, low) 序列 → byte-equal 输出
```

**51 个测试全绿 = 回测可信** ✅
**任一失败 = 立即查，不允许"先跑跑看"**

## 标准 endpoint 行为

`POST /api/v1/backtest/dgr-btc`：

**请求**（v2 schema，无 bar_mode / bar_interval）:
```json
{
  "start_date": "2025-01-01",
  "end_date": "2026-05-23",
  "capital": 10000,
  "grid_step": 0.05,
  "factor": 1.5,
  "max_layers": 5,
  "tp_pct": 0.05,
  "sl_pct": 0.10,
  "fee_pct": 0.0006
}
```

**响应**:
```json
{
  "metrics": {
    "total_return_pct": "...",
    "final_equity": "...",
    "max_drawdown_pct": "...",
    "n_tp": N, "n_sl": M, "n_cycles": K,
    "max_layer_hit": L
  },
  "config_snapshot": {...},
  "n_candles": 730081,
  "elapsed_sec": 47.x
}
```

**数据源**: `/app/state/dgr_btc_klines_1m.csv`（binance 1m OHLC, ~17 月覆盖）

## 唯一权威数字（W7 全期间 2025-01-01 → 2026-05-23）

```
总收益: +11.34%
TP: 43
SL: 5
MaxDD: -23.67%
Cycle: 48
Max Layer: 5/5
耗时: ~47s
```

**任何其他数字（+26.77% / +9.77% / +1.92% / +22.63%）都是 deprecated 模式，不再作为期望值参考**。

## 14 天 paper 预期

```
+11.34% × (14 / 508 天) ≈ +0.31% ≈ +$31 (含波动 -$50 ~ +$80)
```

## 强制规则

1. **任何回测代码改动必须同步过 51 个测试**
2. **回测脚本和 paper_trading 共享 MartingaleEngine + MartingaleBacktestRunner**
3. **不允许引入"优化"绕过真实成交模型**
4. **任何与上面 byte-equal 不一致的结果都是 bug，必须 root cause 修复**

---

参考：
- 回测引擎：`backend/app/strategies/dgr_btc/backtest_runner.py`
- 真实交易 tick：`backend/app/strategies/dgr_btc/paper_trading.py::_tick`
- Engine 核心：`backend/app/strategies/dgr_btc/engine.py`
- 跨窗口验证：`backend/tests/strategies/dgr_btc/test_backtest_consistency.py`
- Mirror 等价性：`backend/tests/strategies/dgr_btc/test_mirror_equivalence.py`
