# 15 · 跨所价差套利策略 · 完整设计

> **策略类型**:`cross_exchange_spread`
> **优先级**:🟡 P2(跟基差套利配套)
> **预期月化收益**:0.3% - 1.0%
> **最大单月回撤**:-2%
> **资金需求**:$5,000 起;实战可扩展至 $200k+
> **策略复杂度**:⭐⭐⭐(中)

---

## 序言:跟基差套利的关系

⚠️ **请先读完第 3 章(跨所基差套利)再读这一章**。本章大量引用第 3 章的概念,只讲**两者的差异**和**独有内容**。

### 0.1 两者的本质差异

| 维度 | 第 3 章·基差套利 | 本章·跨所价差套利 |
|---|---|---|
| 标的 | 永续 vs 季度 (**同所内**不同合约) | 永续 vs 永续 (**不同交易所**同合约) |
| 收益来源 | 基差到期收敛 | 跨所价差回归 |
| 持仓周期 | 30-50 天 | 几小时-几天 |
| 资金费率成本 | 双向(永续 + 季度无费) | 双向(两边都是永续,都付费) |
| 难度 | 季度合约换月复杂 | 跨所资金调度复杂 |

### 0.2 为什么单独做这个策略

**跟第 3 章基差套利不同**:
- 基差套利吃**到期收敛的钱**(季度合约必然在到期日 = 现货价)
- 跨所价差套利吃**两个交易所定价不同步的钱**(没有"到期"机制,靠市场自然回归)

跨所价差套利的机会**比基差套利多**(每个交易所都有微小的定价差异),但**单次利润小**。

---

## 一、策略原理

### 1.1 为什么不同交易所的价格不同

理论上,Binance 上 BTC 永续 $60,000 和 Bybit 上 BTC 永续 $60,000 应该完全相等。**实际不会**:

1. **不同的资金费率**:Binance 资金费率高于 Bybit → Binance 永续价格被压得更低 → 出现价差
2. **不同的流动性结构**:大单在 Binance 更容易吃到好价,Bybit 的滑点大
3. **不同的用户群体**:Bybit 散户多,情绪驱动定价偏离
4. **API 延迟差异**:套利者在不同交易所反应速度不同

**结果**:Binance 的 BTC 永续和 Bybit 的 BTC 永续,**长期存在 0.05%-0.5% 的价差**,偶尔扩大到 1%+。

### 1.2 套利方法

**方向 A**:Binance 贵,Bybit 便宜
```
Binance 做空 1 BTC 永续 ($60,030)
Bybit 做多 1 BTC 永续  ($59,990)
组合 Delta = 0
锁定价差 $40
等待价差收敛
```

**方向 B**:反过来

### 1.3 为什么不能像第 3 章那样"持有到期"

**永续合约没有到期日**——价差不会"必然收敛"。它**可能扩大**(没有强制约束)。

所以这个策略本质上是**赌"价差会回归"**——这是统计性赌注,不是确定性套利。

---

## 二、数学模型

### 2.1 净收益

```
净收益 = 进场价差 - 出场价差
       - Binance 端的资金费率成本(年化)
       - Bybit 端的资金费率收入(年化,因为做空收资金费)
       - 双向手续费(0.04% × 4 = 0.16%)
       - 滑点(估 0.1%)
```

**关键**:**两边都是永续 → 都付/收资金费**。

如果 Binance 做空 + Bybit 做多:
- Binance 端:做空,资金费率高时**收资金费**(好)
- Bybit 端:做多,资金费率高时**付资金费**(坏)
- 通常这两个相互抵消,但需要监控

### 2.2 进场门槛

```
基础成本(手续费 + 滑点):0.26%
资金费率成本(月化):0.5%-2.0%(波动大)

最低进场门槛:0.4%-0.6%
```

**所以系统设定**:价差 > 0.5% 才进场。

### 2.3 实际预期

| 价差 | 月度发生频率 | 单次利润预期 |
|---|---|---|
| 0.10%-0.30% | 持续(几乎一直) | 不操作(亏成本) |
| 0.30%-0.50% | 中(每天数次) | 边缘,不推荐 |
| 0.50%-1.00% | 少(每周数次) | +0.2%-0.4% (per 仓位) |
| 1%-3%(极端) | 罕见(每月几次) | +0.5%-1.5% |

**月化预期**:0.3%-1.0%,$5k 本金 = $15-$50/月。

---

## 三、执行流程

### 3.1 开仓:跨所双腿原子化

跟第 3 章基差套利**结构相同**——但因为是**两个不同的交易所**,执行更难。

```python
async def open_cross_exchange_spread(
    symbol: str,
    long_exchange: str,
    short_exchange: str,
    size_usd: Decimal
):
    """跨所价差套利开仓"""

    # 关键问题:
    # 跨所跟同所不同 — 你下了 Binance 单,需要等 Binance API 响应,
    # 同时 Bybit 那边的 API 是独立的。两边响应时间不同步。

    # 同时挂两腿(并行)
    long_order_task = asyncio.create_task(
        place_limit_order(long_exchange, symbol, "buy", size_usd, timeout=5)
    )
    short_order_task = asyncio.create_task(
        place_limit_order(short_exchange, symbol, "sell", size_usd, timeout=5)
    )

    # 等待
    long_result, short_result = await asyncio.gather(
        long_order_task, short_order_task, return_exceptions=True
    )

    # 处理各种失败情况
    long_success = isinstance(long_result, Order) and long_result.status == "filled"
    short_success = isinstance(short_result, Order) and short_result.status == "filled"

    if long_success and short_success:
        # 全部成交,记录持仓
        await record_position(...)

    elif long_success and not short_success:
        # 只有多头成交 → 立刻市价平多头
        await emergency_close(long_exchange, symbol, "buy")
        await notify("WARN", "跨所开仓部分失败,已紧急平仓多头")

    elif not long_success and short_success:
        # 只有空头成交 → 立刻市价平空头
        await emergency_close(short_exchange, symbol, "sell")
        await notify("WARN", "跨所开仓部分失败,已紧急平仓空头")

    else:
        # 全失败,无需处理
        await notify("INFO", "跨所开仓失败,无敞口")
```

### 3.2 比同所执行更难的原因

1. **API 响应时间不同步**:Binance 100ms 响应,Bybit 300ms 响应,你不能预测哪边先成交
2. **不同的限频策略**:Binance 1200 req/min,Bybit 600 req/min,需要分别管理
3. **不同的订单状态语义**:每家"已成交"的定义略有不同
4. **网络延迟**:你的服务器到 Binance 30ms,到 Bybit 50ms,差异影响时序

**实战建议**:
- 优先选**地理位置接近**的交易所组合(都在东京区域)
- 避免在网络波动时执行
- 每次开仓后 30 秒内,持续监控两边状态是否一致

### 3.3 持仓监控

```python
async def monitor_position(position):
    # 1. 实时计算当前价差
    long_price = await get_mark_price(position.long_exchange, position.symbol)
    short_price = await get_mark_price(position.short_exchange, position.symbol)
    current_spread_pct = (short_price - long_price) / long_price

    # 2. 资金费率成本累计
    long_funding = await get_funding_rate(position.long_exchange, position.symbol)
    short_funding = await get_funding_rate(position.short_exchange, position.symbol)
    net_funding_cost = (long_funding - short_funding) * position.size

    position.cumulative_funding_cost += net_funding_cost

    # 3. 价差收敛判断
    spread_change = current_spread_pct - position.entry_spread_pct
    if spread_change > position.entry_spread_pct * 0.7:
        # 价差收敛 70%,平仓
        await close_position(position, reason="spread_converged")

    # 4. 价差扩大异常
    if spread_change < -position.entry_spread_pct * 1.5:
        # 价差反向扩大 1.5 倍,异常
        await notify("WARN", f"价差反向扩大,当前浮亏 {position.unrealized_pnl}")
        # 但不强制平仓,等待回归

    # 5. 资金费率成本超阈值
    if position.cumulative_funding_cost > position.entry_spread_pct * position.size * 0.5:
        # 资金费成本已经吃掉 50% 的预期利润,提前平仓
        await close_position(position, reason="funding_cost_too_high")
```

---

## 四、出场策略

| 条件 | 阈值 | 理由 |
|---|---|---|
| **价差收敛 70%** | abs(current) < 0.3 × entry | 锁定大部分收益 |
| **资金费成本超半数预期利润** | cumulative_funding > 0.5 × expected_profit | 成本侵蚀利润 |
| **持仓 7 天** | holding_days > 7 | 最长容忍 |
| **价差反向扩大 150%** | abs(current) > 1.5 × entry | 异常,可能预示结构性变化 |

---

## 五、配置文件示例

```yaml
strategy_type: cross_exchange_spread
instance_name: cross_spread_main
enabled: true

capital:
  allocated: 5000             # $5k(资金充裕时)
  reserved_margin_pct: 30.0   # 留 30% 保证金

scanning:
  exchange_pairs:
    - [binance, bybit]
    - [binance, okx]
    - [bybit, okx]

  scan_interval_seconds: 30

  # 只做主流币(流动性好)
  symbols: [BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT]

  min_24h_volume_per_exchange: 100_000_000

entry_rules:
  min_spread_pct: 0.50        # 最小价差 0.5%
  max_position_count: 3
  max_per_position: 1500
  max_per_pair: 1500

  # 资金费率检查
  max_combined_funding_apr: 30  # 两边资金费率净影响 < 30% APR

exit_rules:
  spread_convergence_pct: 70
  max_holding_days: 7
  funding_cost_threshold_pct: 50

execution:
  order_type: limit
  fill_timeout_seconds: 5
  rollback_on_one_leg_fail: true
  use_post_only: false        # 跨所难做 post-only,用普通限价

risk:
  max_leverage: 3             # 两边都不超 3 倍
  geographical_proximity_required: true   # 优先选近的交易所组合

notifications:
  on_opportunity: [toast]
  on_entry: [telegram, toast]
  on_exit: [telegram, toast]
  on_emergency_unwind: [telegram, discord]
  on_funding_cost_alert: [telegram]
```

---

## 六、风险与亏损场景

### 6.1 跨所执行失败(最常见)

**场景**:Binance 那边订单成交,Bybit 那边订单超时 / 拒绝

**亏损方式**:
- 单边敞口
- 立即市价平仓 → 滑点 0.05%-0.3%

**系统保护**:`emergency_unwind`,跟跨所基差套利相同

**实战频率**:正常约 5%-10% 失败率(比同所更高)

### 6.2 资金费率成本翻车

**场景**:开仓时两边资金费率 0.01%(可忽略),持仓 3 天后,Binance 资金费率涨到 0.05%(年化 54%),3 天累计资金费成本超过预期利润

**系统保护**:
- `funding_cost_threshold_pct: 50` — 累计资金费占预期利润 50% 时强制平仓
- 进场时检查双边资金费率综合预测

### 6.3 价差不回归(最严重)

**场景**:你以为是"暂时"的价差,实际是结构性变化(如某交易所宣布上市新功能,长期改变定价模式)

**亏损方式**:
- 价差**永远不回归**,你只能看着浮亏
- 持仓时间被动延长
- 最终强制平仓时锁定亏损

**系统保护**:
- `max_holding_days: 7` — 7 天强制平仓
- 价差反向扩大 150% 触发警告

**真实例子**:2024 年 Q2 Bybit 推出统一账户后,Bybit 上的某些币种长期比 Binance 贵 0.3%-0.5%,持续了 2 个月才稳定

### 6.4 风险总结

| 风险 | 频率 | 单次损失 | 防御 |
|---|---|---|---|
| 跨所执行失败 | 5%-10% | $1-$10 | 90%(立即回滚) |
| 资金费率翻车 | 中 | 0.3%-0.8% | 70%(预算监控) |
| 价差不回归 | 低 | 0.5%-1.5% | 60%(7 天强平) |
| 网络问题影响时序 | 偶发 | 0.05%-0.2% | 80% |

---

## 七、回测和上线

### 7.1 数据需求

- 历史窗口:6 个月
- 数据频率:1 分钟价格
- **需要同时拉取多家交易所的历史数据**

### 7.2 通过标准

| 指标 | 通过标准 |
|---|---|
| **Sharpe** | > 1.2 |
| **Max DD** | < 4% |
| **Win Rate** | > 75% |
| **平均持仓** | < 5 天 |
| **平均失败回滚损失** | < 0.3% |

### 7.3 实盘阶段

```
Stage 1:$500 实盘 / 1 周
  - 仅 BTC,仅 Binance-Bybit 组合
  - 验证跨所执行链路

Stage 2:$2,000 实盘 / 2 周
  - 加 ETH
  - 加 Binance-OKX 组合

Stage 3:$5,000 持续
```

---

## 八、与其他策略的协同

### 8.1 跟资金费率套利:轻微冲突

资金费率套利在 A 所做永续空,跨所价差套利可能在 A 所做永续多/空——**资源互斥**。

**协同方案**:
- 资金费率套利优先级更高
- 跨所价差套利发现资源被占用,跳过该交易所组合

### 8.2 跟跨所基差套利:互补

基差套利做永续 + 季度,价差套利做永续 + 永续。**完全不冲突**,可以同时持有。

---

## 九、技术实现要点

### 9.1 关键模块

```
CrossExchangePriceMonitor    实时监控多交易所同币种价格
SpreadCalculator             价差计算
GeographicProximityChecker   优先选地理位置近的交易所组合
CrossExchangeExecutor        跨所并行下单
LatencyAwareScheduler        根据 API 延迟调整下单时序
```

### 9.2 数据库

复用 `positions` 和 `position_legs` 表(第 8 章)。

`positions.metadata` 中记录跨所信息:
```json
{
  "long_exchange": "bybit",
  "short_exchange": "binance",
  "entry_spread_pct": 0.65,
  "expected_profit_usd": 9.75
}
```

---

## 总结

5 句话:

1. **本质**:不同交易所的同一币种价差偏离 → 套利
2. **跟基差套利的区别**:这个是永续 vs 永续(没到期),基差是永续 vs 季度(有到期)
3. **门槛**:价差 > 0.5% 才进场
4. **难点**:跨所执行同步性 + 资金费率成本预测
5. **位置**:Phase 1 阶段加入,$5k 配置,跟基差套利互补

---

## 你的下一步

接下来第 16 章:永续合约对冲套利(配对交易)
