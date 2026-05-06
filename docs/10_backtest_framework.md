# 10 · 回测框架 · 完整设计

> **范围**:历史数据回测、Paper Trading、策略验证
> **目标**:在投入真金白银之前,**用历史数据证明策略 work**

---

## 一、为什么必须回测

### 1.1 回测的真正价值

**不是**:"我的策略历史能赚 100%,实盘也能!"——这是**虚假的安慰**。

**是**:
1. **筛选**:把"看起来好但实际不 work"的策略干掉
2. **暴露 bug**:发现执行逻辑问题(比如订单顺序错了)
3. **校准参数**:找到合理的进场阈值、出场阈值
4. **建立预期**:知道"这个策略月化大概 1%-2%",避免实盘看到 0.5% 就慌
5. **压力测试**:在历史极端行情(312、519、LUNA、FTX)下能不能存活

### 1.2 回测的局限

我必须老实告诉你:**回测能告诉你"这个策略历史上的表现",但不能保证"未来也这样"**。

**常见的回测陷阱**:

1. **过拟合**:参数调到刚好适合过去 12 个月,但市场变了就失效
2. **幸存者偏差**:只回测了"还活着的币",已经下架的币不在数据里
3. **滑点低估**:回测假设 0.05% 滑点,实盘可能 0.3%
4. **数据偏差**:数据源给的"成交价"可能跟你能拿到的不一样
5. **未来函数**:不小心用了"明天"的数据来决定"今天"的操作(灾难性 bug)

**所以**:回测通过 ≠ 实盘能赚。**回测不通过 = 这策略肯定不能上**。

---

## 二、回测引擎架构

### 2.1 三种回测模式

```
┌────────────────────────────────────────────────────────┐
│ Mode 1: Vectorized 回测(快速,粗略)                  │
│   - 用 pandas / numpy 一次性算所有时间点              │
│   - 速度极快(几秒跑 1 年)                            │
│   - 但模拟订单成交不准                                 │
│   - 适合:初步筛选、参数扫描                          │
└────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│ Mode 2: Event-driven 回测(精确,慢)                  │
│   - 模拟真实时间流,事件按时间戳逐个处理              │
│   - 速度慢(几分钟跑 1 年)                            │
│   - 准确模拟订单簿、滑点、限价单成交                  │
│   - 适合:最终验证、生产前测试                        │
└────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────┐
│ Mode 3: Paper Trading(实盘行情,模拟订单)            │
│   - 接入实时 WebSocket                                │
│   - 真实运行策略代码                                   │
│   - 但订单不真实下,只记录                            │
│   - 速度:1:1 真实时间                                 │
│   - 适合:上实盘前最后一关                            │
└────────────────────────────────────────────────────────┘
```

**开发流程**:Mode 1 (快速筛选) → Mode 2 (精确验证) → Mode 3 (Paper) → 实盘

### 2.2 Event-driven 引擎核心

```
时间线:
  t0:加载历史 Tick
  t1:策略 scan() → 发现机会
  t2:策略 generate_orders() → 提交虚拟订单
  t3:模拟订单簿匹配 → 部分/完全成交
  t4:策略 monitor_position() → 检查持仓
  ...
  tN:策略 close_position() → 平仓
```

```python
class EventDrivenBacktester:

    def __init__(self, strategy: StrategyBase, data_source: BacktestDataSource):
        self.strategy = strategy
        self.data = data_source
        self.virtual_exchange = VirtualExchange()
        self.event_queue = EventQueue()
        self.metrics = MetricsCollector()

    async def run(self, start_date, end_date):
        # 1. 加载所有事件(按时间排序)
        events = self.data.load_events(start_date, end_date)

        # 2. 按时间戳逐个处理
        for event in events:
            self.virtual_clock = event.timestamp

            # 3. 更新虚拟交易所状态(价格、订单簿)
            self.virtual_exchange.process_event(event)

            # 4. 检查待成交的订单
            self.virtual_exchange.match_orders()

            # 5. 调用策略
            if isinstance(event, FundingRateEvent):
                opp = await self.strategy.scan_opportunities()
                if opp:
                    orders = await self.strategy.generate_orders(opp)
                    self.virtual_exchange.place_orders(orders)

            # 6. 监控持仓
            for position in self.strategy.positions:
                action = await self.strategy.monitor_position(position)
                if action == "close":
                    self.strategy.close_position(position)

            # 7. 记录指标
            self.metrics.record(self.virtual_clock, self.strategy.state)

        # 8. 计算回测结果
        return self.metrics.compute_summary()
```

---

## 三、虚拟交易所

回测的核心是**虚拟交易所**——在内存里模拟真实交易所行为:

### 3.1 订单簿模拟

```python
class VirtualOrderBook:

    def __init__(self):
        self.bids: List[Tuple[Decimal, Decimal]] = []  # [(price, size)]
        self.asks: List[Tuple[Decimal, Decimal]] = []

    def update_from_snapshot(self, bids, asks):
        """从历史快照更新订单簿"""
        self.bids = sorted(bids, key=lambda x: -x[0])
        self.asks = sorted(asks, key=lambda x: x[0])

    def simulate_market_order(self, side: Side, size: Decimal) -> Tuple[Decimal, Decimal]:
        """模拟市价单,返回 (平均成交价, 实际滑点)"""
        if side == Side.BUY:
            levels = self.asks
        else:
            levels = self.bids

        remaining = size
        total_cost = Decimal(0)
        for price, available in levels:
            if remaining <= 0:
                break
            fill_size = min(remaining, available)
            total_cost += fill_size * price
            remaining -= fill_size

        if remaining > 0:
            # 订单簿吃穿了
            raise InsufficientLiquidity()

        avg_price = total_cost / size
        mid_price = (self.bids[0][0] + self.asks[0][0]) / 2
        slippage_pct = abs(avg_price - mid_price) / mid_price * 100
        return avg_price, slippage_pct

    def simulate_limit_order(self, side, size, price, current_time, timeout_seconds):
        """模拟限价单 — 这里有学问"""
        # 简单逻辑:
        # 1. 如果限价好于对手盘最佳价 → 立刻吃单成交
        # 2. 否则挂单等待
        # 3. 如果在 timeout 内,价格触及限价 → 成交
        # 4. 超时未成交 → 撤单
        ...
```

### 3.2 限价单成交模拟

**关键问题**:历史 K 线数据只有 OHLCV,**不知道订单簿动态**。

**简化方案**:
- 用 **1 分钟 K 线的 high/low** 估算限价单成交概率
- 如果在持有期内,价格曾经达到限价 → 假设成交
- 否则 → 假设挂单超时

**严格方案**(适用于高频策略):
- 必须用**实际订单簿快照**(每秒级)
- 数据量大,需要专业数据源(如 Tardis.dev)

### 3.3 手续费模拟

```python
FEE_TIERS = {
    "binance": {
        "spot_maker": 0.001,    # 0.1%
        "spot_taker": 0.001,
        "futures_maker": 0.0002,
        "futures_taker": 0.0004,
    },
    "bybit": {...},
    # ...
}

def calculate_fee(exchange: str, instrument: str, is_maker: bool, notional: Decimal):
    fee_key = f"{instrument}_{'maker' if is_maker else 'taker'}"
    fee_rate = Decimal(str(FEE_TIERS[exchange][fee_key]))
    return notional * fee_rate
```

⚠️ **注意**:回测必须用**真实的费率**——你账户的 VIP 等级如果是 0,不能用 VIP 5 的费率。否则会高估收益。

---

## 四、数据需求

### 4.1 数据类型

| 数据 | 频率 | 用于 |
|---|---|---|
| K 线 | 1 分钟 | 价格变动模拟 |
| 资金费率 | 8 小时 | 资金费率套利 |
| 订单簿快照 | 1-10 分钟一次 | 滑点估算 |
| 季度合约价格 | 1 分钟 | 基差套利 |
| 期权链 | 1 小时 | 期权策略 |

### 4.2 数据源

#### 免费数据源

```
Binance API
  - K 线:免费,完整(从 2017 年起)
  - 资金费率:免费(从 2020 年起)
  - 订单簿:实时免费,历史不提供

Bybit API
  - 类似 Binance
  - 资金费率历史较少

OKX API
  - K 线、资金费率免费
  - 季度合约历史较少
```

#### 付费数据源(更专业)

```
Tardis.dev
  - 全交易所订单簿历史
  - 价格:$200+/月起
  - 适合高频策略回测

Kaiko
  - 机构级数据
  - 价格:$$$$ 很贵
  - 适合专业团队

CryptoCompare / Amberdata
  - 中等价格,质量不错
```

### 4.3 数据存储

```python
# scripts/backfill_history.py

async def backfill_funding_rates(exchange: str, symbol: str, start: date, end: date):
    """从交易所拉历史资金费率,存入 TimescaleDB"""
    adapter = get_adapter(exchange)

    current = start
    while current < end:
        batch = await adapter.fetch_historical_funding(symbol, current, limit=1000)
        await db.insert_funding_history(batch)
        current = batch[-1].timestamp + 1

        # 限频
        await asyncio.sleep(1)

async def backfill_klines(exchange, symbol, interval, start, end):
    """K 线回填"""
    # ...
```

**典型回填规模**:
- 1 个币种 1 年 1 分钟 K 线:约 525,600 条 → 50 MB
- 所有 50 个监控币种 × 5 个交易所 × 1 年:约 12 GB

---

## 五、关键指标计算

### 5.1 标准指标

```python
class BacktestMetrics:

    def calculate(self, equity_curve: List[Tuple[datetime, Decimal]]) -> dict:
        returns = self._daily_returns(equity_curve)

        return {
            # 收益类
            "total_return": (equity_curve[-1][1] / equity_curve[0][1] - 1) * 100,
            "cagr": self._cagr(equity_curve),

            # 风险调整
            "sharpe_ratio": self._sharpe(returns, risk_free_rate=0.04),
            "sortino_ratio": self._sortino(returns),
            "calmar_ratio": self._calmar(returns, equity_curve),

            # 回撤
            "max_drawdown_pct": self._max_drawdown(equity_curve),
            "max_drawdown_duration_days": self._max_dd_duration(equity_curve),

            # 交易统计
            "total_trades": self.total_trades,
            "win_rate": self.winning_trades / max(self.total_trades, 1),
            "profit_factor": self.gross_profit / max(self.gross_loss, 0.001),
            "avg_holding_hours": self.total_holding_hours / max(self.total_trades, 1),

            # 成本统计
            "total_fees_paid": self.total_fees,
            "total_slippage_loss": self.total_slippage,
            "fees_as_pct_of_profit": self.total_fees / max(self.gross_profit, 0.001),
        }

    def _sharpe(self, daily_returns, risk_free_rate=0.04):
        excess = daily_returns - risk_free_rate / 365
        return (excess.mean() / excess.std()) * sqrt(365)
```

### 5.2 PnL 归因

回测结束后,**必须**给出 PnL 归因报表:

```
=== 资金费率套利策略 · 回测结果 ===

总收益:+$1,245.32 (+24.9%)
─────────────────────────────────
来源拆解:
  资金费率收入:    +$1,890.55  (+37.8%)
  价格滑点损失:    -$42.10     (-0.84%)
  手续费支出:      -$523.18    (-10.5%)
  滑点损失:        -$79.95     (-1.6%)
─────────────────────────────────
总和:            +$1,245.32
```

如果发现"手续费占了 40% 的毛收益",这是危险信号——稍微一波动就亏。

---

## 六、压力测试

### 6.1 必跑的极端行情场景

```python
STRESS_TEST_PERIODS = [
    ("2020-03-12", "2020-03-15"),   # 312 黑天鹅
    ("2021-05-19", "2021-05-22"),   # 519 大跌
    ("2022-05-08", "2022-05-15"),   # LUNA 崩盘
    ("2022-11-08", "2022-11-15"),   # FTX 倒闭
    ("2024-08-05", "2024-08-08"),   # 8月5日全球股市黑色星期一
]

for start, end in STRESS_TEST_PERIODS:
    result = backtester.run(start, end)
    print(f"{start} ~ {end}: PnL={result.total_return:.2f}%, MaxDD={result.max_dd:.2f}%")
```

**通过标准**:
- 每段极端行情期间最大回撤 **不超过 5%**
- 没有一次"清算事件"
- 系统正确触发了风控熔断

### 6.2 蒙特卡洛模拟

```python
# 把历史交易顺序打乱,跑 1000 次
# 看 95% 置信区间的回撤范围

def monte_carlo_stress(trades: List[Trade], iterations=1000):
    results = []
    for _ in range(iterations):
        shuffled = random.sample(trades, len(trades))
        cumulative = simulate_equity_curve(shuffled)
        max_dd = calculate_max_drawdown(cumulative)
        results.append(max_dd)

    return {
        "p50_drawdown": np.percentile(results, 50),
        "p95_drawdown": np.percentile(results, 95),
        "p99_drawdown": np.percentile(results, 99),
    }
```

**意义**:即使你历史回测最大回撤 5%,**统计上 99% 概率会出现 8%-10% 的回撤**(因为运气好坏)。

---

## 七、过拟合检测

### 7.1 样本内 vs 样本外

**铁律**:**用一段时间调参,用另一段时间验证**。

```python
# 错误做法:用全部 2 年数据调参 + 验证
# 这是"作弊",回测看起来很美

# 正确做法:
in_sample = ("2024-01-01", "2025-06-30")    # 用这段调参
out_sample = ("2025-07-01", "2025-12-31")   # 用这段验证

# 1. 在 in_sample 上调参,找到最优参数
best_params = grid_search(in_sample)

# 2. 用最优参数,在 out_sample 上跑
out_sample_result = backtest(out_sample, best_params)

# 3. 通过标准:
#    - in_sample Sharpe 和 out_sample Sharpe 差距 < 30%
#    - 否则说明过拟合
```

### 7.2 Walk-Forward 验证

```
更严格的做法:
  ┌─[训练]─┬─[测试]─┐
  │ 12 月  │ 3 月    │
  └────────┴─────────┘
       ┌─[训练]─┬─[测试]─┐
       │ 12 月  │ 3 月    │
       └────────┴─────────┘
            ┌─[训练]─┬─[测试]─┐
            │ 12 月  │ 3 月    │
            └────────┴─────────┘

每次窗口前移,每次都要"重新训练 + 测试"。
所有 walk-forward 测试期间都要 Sharpe > 1.0,
才算"参数稳健"。
```

---

## 八、Paper Trading

### 8.1 与回测的差异

| 维度 | 回测 | Paper Trading |
|---|---|---|
| 数据 | 历史 | 实时 |
| 时间 | 加速 | 1:1 真实 |
| 订单 | 模拟 | 真实下单 API,但用 testnet |
| 风险 | 0 | 0 |
| 周期 | 几小时跑完 | 至少 2-4 周 |

### 8.2 Paper Trading 检查清单

```
☐ 系统连续运行 14 天无崩溃
☐ 模拟订单成交率 > 90%
☐ 实际收益与回测预测的差距 < 30%
☐ 触发熔断时系统正确停机
☐ 所有通知渠道正常
☐ Delta 偏离监控正常工作
☐ 资金费结算时刻系统正确处理
☐ 至少经历 1 次"资金费率反转",系统正确平仓
☐ 至少经历 1 次"模拟 API 错误",系统正确处理
☐ PnL 归因报表正确(收益来源、手续费、滑点拆分)
```

---

## 九、回测报告

### 9.1 输出格式

```markdown
# 回测报告 · 资金费率套利

**回测周期**:2024-01-01 ~ 2025-12-31
**初始资金**:$10,000
**结束资金**:$11,234

## 核心指标

| 指标 | 值 | 通过 |
|---|---|---|
| Sharpe | 1.62 | ✅ (>1.5) |
| Max DD | 6.4% | ✅ (<8%) |
| Win Rate | 73% | ✅ (>70%) |
| Total Trades | 287 | ✅ (>200) |

## 月度收益

[折线图]

## PnL 归因

[饼图]

## 极端行情表现

| 时段 | 收益 | 最大回撤 |
|---|---|---|
| 2024-08-05 黑色星期一 | -1.2% | 1.4% |
| 2024-Q4 牛市 | +8.5% | 0.8% |

## 失败的交易分析

最大单笔亏损:-2.3%
失败原因排名:
1. 资金费率突然反转(8 次)
2. API 错误未及时处理(2 次)
3. 滑点超预期(1 次)

## 结论

✅ 通过回测,可以进入 Paper Trading
```

---

## 十、目录结构

```
backtest/
├── __init__.py
├── engine.py                  -- 回测引擎主类
├── modes/
│   ├── vectorized.py          -- 模式 1
│   ├── event_driven.py        -- 模式 2
│   └── paper_trading.py       -- 模式 3
├── virtual_exchange.py        -- 虚拟交易所
├── data_loader.py             -- 历史数据加载
├── metrics.py                 -- 指标计算
├── stress_test.py             -- 压力测试
├── walk_forward.py            -- Walk-forward 验证
├── monte_carlo.py             -- 蒙特卡洛
├── reports/
│   ├── html_report.py         -- HTML 报告生成
│   ├── pdf_report.py          -- PDF 报告
│   └── templates/
└── tests/
```

---

## 十一、总结

5 句话:

1. **回测三种模式**:vectorized 快速 → event-driven 精确 → paper trading 1:1 模拟
2. **数据是基础**:免费 API + 付费数据源,12 个月以上历史数据
3. **关键指标**:Sharpe > 1.5、Max DD < 8%、Win Rate > 70%
4. **必须压力测试**:312、519、LUNA、FTX 这些极端行情都要跑
5. **避免过拟合**:样本内调参 + 样本外验证,Walk-forward 是金标准

---

## 你的下一步

1. 接下来最后一篇:第 11 篇 部署 + 应急手册
2. 之后我把全部 11 份文档合并成 PDF 给你
