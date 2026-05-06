# 13 · 三角套利策略 · 完整设计

> **策略类型**:`triangular_arb`
> **优先级**:🟢 P1(Phase 0 阶段第二个上线的策略,资金费率套利之后)
> **预期月化收益**:0.3% - 0.8%
> **最大单月回撤**:-1%
> **资金需求**:Phase 0 阶段 $500;实战可扩展至 $50k+
> **策略复杂度**:⭐⭐(中低)

---

## 序言:为什么要做三角套利

虽然月化收益(0.3%-0.8%)比资金费率套利(1%-2%)低,但三角套利对你的价值是:

1. **训练系统的执行能力**:这是个**纯执行型**策略,逻辑简单但对延迟、原子性要求高,做完它你的执行系统就成熟了
2. **小资金友好**:$500 起就能跑,适合 Phase 0 早期验证
3. **风险极低**:Delta 全程为 0,无方向性敞口
4. **机会数量多**:每天可能有几十-几百次机会,适合**高频小额**操作

**老实交代**:三角套利不是赚大钱的策略,但是个**好的"练手"策略**——用最小的风险跑通整个系统。

---

## 一、什么是三角套利

### 1.1 核心原理

加密货币交易所有几百种交易对(BTC/USDT、ETH/USDT、ETH/BTC...)。**理论上**,这些交易对的价格之间应该满足:

```
ETH/USDT × USDT/BTC = ETH/BTC

例如:
  ETH/USDT = 3000
  BTC/USDT = 60000
  按理:ETH/BTC = 3000 / 60000 = 0.05

  实际:ETH/BTC = 0.0501
```

如果实际汇率不等于理论汇率,就有套利机会:

```
路径 A(顺时针):
  USDT → BTC → ETH → USDT
  起始 1000 USDT
  → 1000/60000 = 0.01667 BTC
  → 0.01667/0.0501 = 0.3327 ETH
  → 0.3327 × 3000 = 998.01 USDT
  净收益:-1.99 USDT(亏)

路径 B(逆时针):
  USDT → ETH → BTC → USDT
  起始 1000 USDT
  → 1000/3000 = 0.3333 ETH
  → 0.3333 × 0.0501 = 0.01670 BTC
  → 0.01670 × 60000 = 1002.05 USDT
  净收益:+2.05 USDT(赚)
```

**这个就是三角套利**:在同一个交易所,通过 3 次连续交易,利用价格不一致赚取小额价差。

### 1.2 为什么会出现价差

理论上做市商会立刻把价格拉平,**实际不会**:

1. **执行速度**:即使做市商也有几百毫秒延迟
2. **不同交易对的流动性**:ETH/USDT 流动性极好,但 ETH/BTC 流动性可能差,价格更新慢
3. **手续费阶梯**:做市商也要扣手续费,他们不会把价差完全消除到 0
4. **极端行情**:行情剧烈波动时,各交易对价格更新不同步

**结果**:每天都会出现几十到几百次"理论可套利"的机会,平均存在时间 **0.1-2 秒**。

---

## 二、套利路径设计

### 2.1 经典三角(USDT-基础)

最常见的三角:

```
   USDT
    │ ↘
    │   BTC
    │  ↙
   ETH
```

**所有从 USDT 出发,经过 BTC 和 ETH,回到 USDT 的路径**:
- 路径 1:USDT → BTC → ETH → USDT
- 路径 2:USDT → ETH → BTC → USDT

**系统会同时监控两条路径**,谁赚钱做谁。

### 2.2 多种类三角

每个交易所支持几十-几百个交易对,可以构造大量三角:

**主流币三角**(流动性极好,价差小但稳定):
- USDT-BTC-ETH
- USDT-BTC-BNB(只在 Binance)
- USDT-ETH-BNB
- USDT-BTC-SOL
- USDT-ETH-SOL

**稳定币三角**(几乎无方向性风险):
- USDT-USDC-BTC
- USDT-DAI-ETH
- USDT-FDUSD-BTC(只在 Binance)

**长尾币三角**(价差大但风险高):
- USDT-BTC-DOGE
- USDT-ETH-PEPE

⚠️ **优先做主流币三角**——长尾币三角看着诱人,但流动性陷阱很多。

### 2.3 路径自动发现

系统启动时,自动构造所有可能的三角路径:

```python
def discover_triangular_paths(exchange: str) -> List[TriangularPath]:
    """
    自动发现所有 USDT-基础的三角路径
    """
    pairs = get_all_trading_pairs(exchange)
    paths = []

    # 找所有 USDT 计价的币种
    usdt_quoted = [p.base for p in pairs if p.quote == "USDT"]

    # 找两两之间存在的交易对
    for coin_a in usdt_quoted:
        for coin_b in usdt_quoted:
            if coin_a == coin_b:
                continue
            # 检查 coin_a/coin_b 这个对是否存在
            cross_pair = find_pair(exchange, coin_a, coin_b)
            if cross_pair:
                # 构造两条路径
                paths.append(TriangularPath(
                    leg1=("USDT", coin_a),    # 买 coin_a
                    leg2=(coin_a, coin_b),    # 用 coin_a 买 coin_b
                    leg3=(coin_b, "USDT"),    # 卖 coin_b 回 USDT
                ))
                paths.append(TriangularPath(
                    leg1=("USDT", coin_b),
                    leg2=(coin_b, coin_a),
                    leg3=(coin_a, "USDT"),
                ))

    return dedupe(paths)
```

Binance 这种大所大概能发现 **200-500 条**有效三角路径。

---

## 三、数学模型

### 3.1 套利净收益

```
净收益 = 1 - (1 - fee)^3 × 路径回报率
```

其中:
- `fee` = 单边手续费率(Binance taker 0.1%)
- 路径回报率 = leg1 × leg2 × leg3

### 3.2 进场门槛

考虑手续费,**最低进场门槛**:

```
单边 taker 手续费:0.1%
3 笔交易总手续费:1 - (1 - 0.001)^3 ≈ 0.30%

加上滑点和延迟成本(估 0.05%)
最低进场门槛:0.35%
```

也就是说,**理论汇率偏离 0.35% 以上才值得做**。

### 3.3 用 Maker 单的极限

如果用 BNB 抵扣 + Maker 单(Binance VIP 0):
- Maker 费率:0.075%(BNB 抵扣后)
- 3 笔总手续费:0.225%
- 最低进场门槛:**0.27%**

**但是**:Maker 单不一定能成交,会错过机会。**实战中,大部分三角套利用 Taker 单**,牺牲手续费换确定性。

### 3.4 不同币种的预期收益

| 币种组合 | 平均机会频率 | 平均价差 | 月度可赚次数 | 单次预期 |
|---|---|---|---|---|
| BTC-ETH 三角 | 高(>100 次/天) | 0.05%-0.15% | 较少能突破门槛 | $0(打平) |
| BTC-BNB-ETH 三角 | 中(10-30 次/天) | 0.10%-0.30% | 50-100 次/月 | +$0.5-2 |
| 稳定币三角 | 中(20-50 次/天) | 0.05%-0.20% | 100-200 次/月 | +$0.3-1 |
| 长尾币三角 | 低(1-10 次/天) | 0.30%-2.0% | 10-30 次/月 | +$2-15 |

**月度合理预期**($1000 本金):
- 主流币三角:**+$3 到 +$10**(0.3%-1%)
- 稳定币三角:**+$2 到 +$8**
- 长尾币三角(谨慎):**+$5 到 +$30**

**总计**:**+$10 到 +$50 / 月**($1000 本金,即 1%-5% 月化)。**实际平均落在 0.3%-0.8% 月化**。

---

## 四、执行流程(关键!)

### 4.1 三笔订单的"原子化"

三角套利**最致命的风险**:某一笔订单成交了,后面失败了——你被困在中间币种。

**例如**:USDT → BTC → ETH → USDT
- Step 1:USDT 买 BTC,**成交**
- Step 2:BTC 买 ETH,**因为流动性不足失败**
- 结果:你**持有 BTC**,但策略期望你持有 USDT,**你被迫做方向性赌博**

### 4.2 处理方案对比

**方案 1:全用市价单(简单但贵)**
```
3 笔市价单同时打出
✅ 100% 能成交
❌ 滑点最大
❌ 实际收益经常被吃掉
```

**方案 2:全用限价单(便宜但风险大)**
```
3 笔限价单同时挂
✅ 滑点低
❌ 部分成交、部分挂着 = 部分敞口风险
❌ 价差转瞬即逝,等不到全部成交
```

**方案 3:Taker 单串行(我们采用)**
```
Step 1:Taker 单立刻吃单
Step 2:Step 1 成交后,立刻 Taker 吃单
Step 3:Step 2 成交后,立刻 Taker 吃单
任意一步失败 → 立刻反向平仓被困的中间币种
```

我们用方案 3。理由:
- 比方案 1 的滑点小(因为 Step 1-3 之间每次都重新评估订单簿)
- 比方案 2 的不确定性小(每步都用 Taker,基本能成交)

### 4.3 失败回滚机制

```python
async def execute_triangular(path: TriangularPath, amount_usdt: Decimal):
    intermediate_coin = None
    intermediate_amount = Decimal(0)

    try:
        # Step 1: USDT → coin_a
        order1 = await place_taker_order(
            symbol=f"{path.coin_a}/USDT", side="buy", amount=amount_usdt
        )
        if order1.status != "filled":
            raise StepFailed("Step 1 not filled")
        intermediate_coin = path.coin_a
        intermediate_amount = order1.filled_amount

        # Step 2: coin_a → coin_b
        order2 = await place_taker_order(
            symbol=f"{path.coin_a}/{path.coin_b}",
            side="sell", amount=intermediate_amount
        )
        if order2.status != "filled":
            raise StepFailed("Step 2 not filled")
        intermediate_coin = path.coin_b
        intermediate_amount = order2.filled_amount

        # Step 3: coin_b → USDT
        order3 = await place_taker_order(
            symbol=f"{path.coin_b}/USDT", side="sell", amount=intermediate_amount
        )
        if order3.status != "filled":
            raise StepFailed("Step 3 not filled")

        # 计算实际收益
        final_usdt = order3.filled_amount * order3.avg_price
        profit = final_usdt - amount_usdt
        return profit

    except StepFailed as e:
        # 回滚:把当前持有的 coin 卖回 USDT
        if intermediate_coin and intermediate_amount > 0:
            await emergency_sell_to_usdt(intermediate_coin, intermediate_amount)
            await notify("WARN", f"三角套利失败,已紧急平仓 {intermediate_coin}")
        raise
```

### 4.4 重要的实现细节

#### 不要用市价单的原因

很多教程教用市价单,但**实战中市价单很危险**:

- Binance 等大所对市价单有"保护性限制"——如果市价偏离参考价超过某阈值,直接拒绝
- 高波动时,市价单可能在订单簿上吃出 1%+ 的滑点
- **我们用 IOC limit 单**(Immediate-or-Cancel 限价单)代替市价单

```python
async def place_taker_order(symbol, side, amount):
    """Taker 单的安全实现"""
    # 1. 拿当前最优对手价
    orderbook = await fetch_orderbook(symbol, depth=5)
    if side == "buy":
        # 用当前最优卖价 + 微量安全边际
        price = orderbook.asks[0].price * Decimal("1.0005")
    else:
        price = orderbook.bids[0].price * Decimal("0.9995")

    # 2. 用 IOC 限价单(立刻成交或全部取消)
    return await exchange.place_order(
        symbol=symbol,
        side=side,
        amount=amount,
        price=price,
        type="limit",
        time_in_force="IOC"
    )
```

#### 时间戳同步

三笔订单**总耗时必须 < 1 秒**,否则套利窗口关闭:

```python
# 性能要求
Step 1 下单 + 成交确认:< 200ms
Step 2 下单 + 成交确认:< 200ms
Step 3 下单 + 成交确认:< 200ms
总计:< 600ms,留 400ms 余量
```

如果你的 VPS 到 Binance 服务器的网络延迟 > 50ms,**这个策略根本做不出来**——必须把服务器放到东京 / 新加坡。

---

## 五、扫描与触发

### 5.1 实时扫描循环

```python
async def scan_loop(exchange: str):
    paths = await discover_triangular_paths(exchange)

    while True:
        for path in paths:
            # 1. 拿最新行情
            ticker_a = await get_ticker(f"{path.coin_a}/USDT")
            ticker_b = await get_ticker(f"{path.coin_b}/USDT")
            cross = await get_ticker(f"{path.coin_a}/{path.coin_b}")

            # 2. 计算路径收益
            theoretical_rate = ticker_a.bid / ticker_b.ask  # 理论汇率
            actual_rate = cross.bid                          # 实际汇率
            spread_pct = (actual_rate - theoretical_rate) / theoretical_rate

            # 3. 判断是否触发
            if spread_pct > MIN_SPREAD_THRESHOLD:
                await trigger_arbitrage(path, direction="forward")
            elif spread_pct < -MIN_SPREAD_THRESHOLD:
                await trigger_arbitrage(path, direction="reverse")

        await asyncio.sleep(0.05)  # 50ms 一轮
```

### 5.2 用 WebSocket 而非轮询

REST API 拉行情每秒最多几次,不够。**必须用 WebSocket**:

```python
# 订阅所有相关币对的实时行情
async def subscribe_tickers(exchange: str):
    pairs = collect_all_pairs_in_paths()
    for pair in pairs:
        await ws.subscribe(f"{pair}@ticker")

    # 推送回调
    @ws.on_ticker
    async def on_ticker_update(symbol, bid, ask):
        ticker_cache[symbol] = (bid, ask, time.time())
        # 触发依赖此 symbol 的所有路径检查
        affected_paths = path_index[symbol]
        for path in affected_paths:
            check_path_opportunity(path)
```

---

## 六、出场策略

三角套利**没有"出场"概念**——单次执行 1-2 秒内完成,不持有头寸。

但有几个例外情况:
- **Step 失败**:被困中间币种 → 立刻紧急平仓
- **Step 部分成交**:留部分中间币种 → 立刻平掉残量
- **执行后期发现总利润 < 0**(比如滑点超预期):记录,优化未来阈值

---

## 七、配置文件示例

```yaml
strategy_type: triangular_arb
instance_name: triangular_main
enabled: true

capital:
  allocated: 500              # Phase 0 阶段 $500 起步
  per_execution_size: 100     # 单次最多 $100,分多次执行
  max_concurrent_executions: 3

scanning:
  exchanges: [binance, bybit, okx]
  scan_interval_ms: 50        # 50ms 一轮

  # 路径筛选
  base_quote: USDT
  cross_coins: [BTC, ETH, BNB, SOL, USDC, FDUSD]
  exclude_pairs: []           # 黑名单
  min_24h_volume_usd: 50_000_000  # 流动性要求

entry_rules:
  min_spread_pct: 0.35        # 最低 0.35% 才触发
  use_taker_orders: true      # Taker 单
  max_slippage_per_step: 0.10 # 单步滑点超 0.10% 取消

execution:
  step_timeout_ms: 200        # 单步 200ms timeout
  total_timeout_ms: 800       # 总耗时 800ms
  emergency_unwind: true      # 失败时强制反向平仓
  max_unwind_loss_pct: 0.5    # 最大允许平仓亏损 0.5%

risk:
  max_per_path: 200           # 单路径单次 $200
  daily_loss_limit_usd: 30    # 单日亏损超 $30 暂停
  consecutive_failures_halt: 5  # 连续 5 次失败暂停

notifications:
  on_opportunity: [toast]     # 机会多,只发 Toast
  on_execution_success: [toast]
  on_execution_failure: [telegram, toast]
  on_emergency_unwind: [telegram, discord]
  on_daily_summary: [telegram]
```

---

## 八、风险与亏损场景

### 8.1 中间币种被困(最严重)

**场景**:Step 1 成交,Step 2 因为对手盘消失失败。你持有 BTC,但策略期望持有 USDT。

**亏损方式**:
- 紧急平仓 BTC 回 USDT,可能亏 0.05%-0.5%(滑点)
- 如果当时市场剧烈波动,可能亏 1%-2%

**系统保护**:
- `emergency_unwind: true` — 立刻反向平仓
- `max_unwind_loss_pct: 0.5` — 单次平仓亏损不超 0.5%

**防不住的部分**:极端行情下(几秒内币价跳 5%),平仓也来不及

**实战频率**:正常情况下 1 万次执行可能发生 50-100 次(0.5%-1% 失败率)

### 8.2 价格滞后(最常见)

**场景**:你扫到机会时实际可成交价已经变了,实际利润 < 预期。

**亏损方式**:
- 不亏钱,但**赚得比预期少**或**完全打平**
- 频繁发生

**系统保护**:
- `max_slippage_per_step: 0.10` — 单步滑点超 0.10% 取消订单
- 总滑点超阈值不执行

**实战频率**:**80%-90% 的"机会"扫到后实际不能赚到钱**——这是正常的

### 8.3 被做市商"反向操作"

**场景**:你的下单进入订单簿,做市商立刻调整自己的报价,你的对手盘消失。

**亏损方式**:
- 你以为能 0.001 BTC 买到的 ETH,做市商把价格调高到 0.0011
- 你只能放弃这次机会(滑点保护拦下来)

**系统保护**:
- IOC 单(立刻成交或全部取消)
- 不会留挂单等做市商攻击

**防不住的部分**:专业做市商有 co-located 服务器,你**永远比他们慢**

### 8.4 交易所限频

**场景**:你 50ms 扫一轮 + 发现机会立刻下单,REST API 调用频率超出限制。

**亏损方式**:
- 被限频后无法下单,错过机会
- 极端情况下 IP 被临时禁止

**系统保护**:
- 用 WebSocket 拿行情(不计入 REST 限频)
- 下单走 REST,但有应用层 rate limiter
- 监控 `X-MBX-USED-WEIGHT` 头,接近上限自动降速

**实战建议**:
- Binance:1200 req/min,我们留 50% 余量,800 req/min 上限
- 优先在小币种 / 冷门交易所做(限频压力小)

### 8.5 风险总结

| 风险 | 频率 | 单次损失 | 系统能防多少 |
|---|---|---|---|
| 中间币种被困 | 0.5%-1% 执行 | $1-$10 | 95%(立即平仓) |
| 价格滞后 | 80%+ 扫到的机会 | $0(只是不赚) | 100%(滑点保护) |
| 做市商反操作 | 持续存在 | $0(取消订单) | 100% |
| 限频 | 配置不当时 | 错过机会 | 100%(限频管理) |

**整体风险评估**:
- **最大单笔亏损**:< $10(per $200 本金)
- **月度最大亏损**:< $30(per $500 本金,即 -6%)
- **远低于其他策略**

---

## 九、回测要求

### 9.1 数据需求

- **历史窗口**:**至少 3 个月**(三角套利机会时效短,不需要长历史)
- **数据频率**:**每秒级订单簿快照**(关键!K 线没用)
- **数据源**:Tardis.dev 或自己用 WS 录制

### 9.2 关键挑战

**回测和实盘的差异比其他策略大**:
- 你看到的"历史价差"可能只存在 0.3 秒
- 你的真实下单需要 200-500ms
- 所以"看着可以赚的机会",实际可能根本来不及

**正确的回测方法**:
- 模拟你的真实延迟(VPS 到交易所 50-100ms)
- 模拟订单簿被你"消耗"后的状态
- 模拟 IOC 单未成交的概率(估 30%)

### 9.3 通过标准

| 指标 | 通过标准 |
|---|---|
| **Sharpe** | > 2.0(高频策略要求高) |
| **Win Rate** | > 95%(执行成功率,不是利润胜率) |
| **失败率** | < 5% |
| **平均执行时间** | < 800ms |
| **月化收益** | > 0.3%(否则不值得做) |

---

## 十、Paper Trading + 实盘

### 10.1 Paper Trading

- **运行时长**:**至少 1 周**(高频,样本充足)
- **必须经历**:至少 100 次成功执行 + 5 次失败回滚

### 10.2 实盘阶段

```
Stage 1:$100 实盘 / 3 天
  - 仅做 1 个交易所(Binance)
  - 仅做 BTC-ETH-USDT 三角
  - 验证执行链路 + 真实滑点

Stage 2:$200 实盘 / 1 周
  - 加 BNB / SOL 三角
  - 验证多路径并发

Stage 3:$500 实盘 / 持续
  - Phase 0 目标资金量
  - 加多交易所
```

每阶段切换需要前一阶段 100% 通过。

---

## 十一、与其他策略的协同

### 11.1 跟资金费率套利:几乎不冲突

资金费率套利持仓时间长(几天-几周),三角套利单次 1-2 秒。**完全不同时间尺度**。

**唯一冲突**:都用 USDT 余额。
- 资金费率套利占用 USDT $2000
- 三角套利占用 USDT $500
- 总 USDT 需求 $2500,留 20% 缓冲 = $3000 USDT 在交易所

### 11.2 跟期现套利:**严重冲突**

期现套利和三角套利**都用现货 + 都看价差**。可能出现:
- 期现套利想做 BTC 现货多
- 三角套利同一时刻也要买 BTC 现货
- 两边抢余额

**协同方案**:
- 三角套利的"中间币种"是临时持有(< 1 秒),不算长期持仓
- Position Manager 不需要锁三角套利的资源
- 但需要监控:三角套利在 0.5 秒内的临时持有,期现套利不要在同一币上同时操作

---

## 十二、技术实现要点

### 12.1 关键模块

```
TriangularPathDiscovery        启动时构造所有可行三角路径
TriangularScanner              50ms 扫描循环
PathOpportunityCalculator      每个路径的收益计算
TriangularExecutor             3 笔订单的串行执行
EmergencyUnwinder              失败回滚
LatencyMonitor                 监控 VPS 到交易所的延迟
```

### 12.2 性能优化

```python
# 1. 行情数据缓存(避免重复 API 调用)
ticker_cache = {}  # 内存中,每 50ms 由 WS 更新

# 2. 路径索引(O(1) 找到受影响的路径)
path_index = {symbol: [path1, path2, ...]}

# 3. 提前计算汇率
def precompute_implied_rate(path, ticker_cache):
    """汇率计算用 Decimal 太慢,用 float 加速"""
    return float(ticker_cache[path.leg1_symbol].bid) * \
           float(ticker_cache[path.leg2_symbol].bid)

# 4. 异步并发下单(虽然是串行执行,但请求构造可并行)
```

### 12.3 数据库扩展

复用 `positions` 和 `orders` 表(在第 8 章定义),新增 `strategy_type='triangular_arb'`。

新增专用表:

```sql
CREATE TABLE triangular_executions (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL,

    strategy_instance VARCHAR(100) NOT NULL,
    exchange VARCHAR(30) NOT NULL,

    path_definition JSONB NOT NULL,      -- 完整路径
    direction VARCHAR(20),                -- forward/reverse

    initial_amount_usd DECIMAL(20, 8),
    final_amount_usd DECIMAL(20, 8),
    profit_usd DECIMAL(20, 8),
    profit_pct DECIMAL(10, 4),

    -- 执行细节
    leg1_order_id BIGINT,
    leg2_order_id BIGINT,
    leg3_order_id BIGINT,

    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_ms INT,

    status VARCHAR(20),  -- success/partial_failure/full_failure
    failure_step INT,
    failure_reason TEXT,
    unwound BOOLEAN DEFAULT FALSE,
    unwind_loss_usd DECIMAL(20, 8)
);
```

---

## 总结

5 句话:

1. **本质**:用 3 笔交易吃同一交易所内不同币对之间的微小价差
2. **门槛**:扣手续费后 0.35% 才值得做
3. **关键**:执行速度,3 笔订单总耗时 < 800ms
4. **风险**:中间币种被困,但有紧急回滚机制
5. **位置**:Phase 0 阶段第 2 个上线,$500 起步,月化 0.3%-0.8%

---

## 你的下一步

1. 接下来第 14 章:稳定币套利(简短版)
