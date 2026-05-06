# 17 · DEX LP + 对冲策略 · 完整设计

> **策略类型**:`dex_lp_hedged`
> **优先级**:🟡 P2(Phase 1+ 阶段考虑)
> **预期月化收益**:1.0% - 3.0%
> **最大单月回撤**:-5%
> **资金需求**:**$10,000 起**(资金门槛较高)
> **策略复杂度**:⭐⭐⭐⭐(高)

---

## 序言:必读的丑话

按你的开发流程要求(老实交代风险),我必须先讲清楚:

### 0.1 这个策略的"高收益"是有代价的

DEX LP + 对冲策略宣传的"年化 30%-100%" **大部分是误导**。真实情况:

- 表面 APY:30%-100%(LP 池子展示的数字)
- 扣除无常损失(IL):**-30% 到 -50% 收益**
- 扣除对冲成本:**-10% 到 -20% 收益**
- 真实年化:**12%-36%**(月化 1%-3%)

**比直接持有 USDT 借出去赚 5% 的策略高,但远没有宣传的那么夸张**。

### 0.2 IL(无常损失)是这个策略的核心难点

IL 不是简单的"币价跌了你亏钱"。它是**数学结构性**的——只要价格波动,你就会亏。

我会在第二节详细讲 IL 的数学,**这是这个策略最重要的概念**。如果你看不懂 IL,**不要做这个策略**。

### 0.3 智能合约风险

跟其他策略不同,这个策略**直接跟智能合约交互**:
- 合约可能有 bug(资金归零)
- 合约可能被黑客攻击(2024 年 DEX 被盗超过 $5 亿)
- 治理攻击(不太可能但发生过)

**我们只用最成熟的协议**:Uniswap V3、PancakeSwap V3、Curve。**不做新协议**。

### 0.4 适合的人

这个策略**不适合**:
- 不懂 DeFi 的人
- 资金 < $10k 的人(gas 费占比太高)
- 不能 24 小时盯仓的人(IL 会快速恶化)

**Phase 0 阶段不要做这个**。Phase 1 资金 $30k+ 后,可以拨 $10k 试水。

---

## 一、什么是 DEX LP

### 1.1 自动做市商 (AMM) 基础

传统交易所用**订单簿**——买卖双方挂单,撮合成交。

DEX(去中心化交易所)如 Uniswap 用 **AMM(自动做市商)**——没有订单簿,而是流动性池子。

**最简单的 AMM 公式(Uniswap V2)**:
```
x × y = k

x = 池子里 token A 的数量
y = 池子里 token B 的数量
k = 常数

每次交易后 k 保持不变
```

**例子**:
- 池子里有 100 ETH 和 300,000 USDC
- k = 100 × 300,000 = 30,000,000
- 当前价格:300,000 / 100 = $3,000 per ETH

**用户买入 ETH**(用 USDC 换 ETH):
- 用户付 30,000 USDC
- 池子里 USDC 变成 330,000
- 由 k 不变:ETH = 30,000,000 / 330,000 = 90.91
- 用户拿走 9.09 ETH(平均价 30,000 / 9.09 = $3,300/ETH,比初始价高)

这就是**滑点的来源**。

### 1.2 流动性提供者(LP)

任何人可以**向池子里存入资产**,成为 LP:
- 必须**等价值**存入两种资产(比如 50% ETH + 50% USDC)
- 拿到 LP token,代表你的份额
- 每次有人交易,你按份额分**手续费**

**例子**:
- 池子总价值 $600,000(100 ETH + 300,000 USDC)
- 你存入 1 ETH + 3,000 USDC = $6,000(占池子 1%)
- 池子手续费费率 0.30%
- 池子每天交易量 $1,000,000
- 池子每天手续费收入 $3,000
- 你的份额收入:$3,000 × 1% = $30/天 = $900/月(15% 月化!)

**这看起来很美好**。但下面就要讲 IL。

---

## 二、无常损失(IL)的数学

### 2.1 直观理解

**IL 的本质**:作为 LP,**当价格波动时,池子会"自动"用便宜的资产换走你的贵资产,卖给套利者**。

**例子**:
- 你存入 1 ETH ($3,000) + 3,000 USDC,总价值 $6,000
- ETH 价格涨到 $6,000(翻倍)
- 套利者发现池子里 ETH 便宜(因为价格还没更新),买入大量 ETH
- 池子里 ETH 减少,USDC 增加
- 你的份额变成:**0.71 ETH + 4,242 USDC = $8,486**

**对比**:
- 如果你不做 LP,只持有原资产:1 ETH + 3,000 USDC = **$6,000 + $3,000 = $9,000**
- 做 LP:$8,486
- **IL = $9,000 - $8,486 = $514(损失 5.7%)**

### 2.2 IL 的数学公式

价格变化倍数 r(=新价格/旧价格)对应的 IL:

```
IL = 2 × √r / (1 + r) - 1
```

| 价格变化 | IL |
|---|---|
| 1.25× (+25%) | -0.6% |
| 1.5× (+50%) | -2.0% |
| 2× (翻倍) | -5.7% |
| 3× | -13.4% |
| 4× | -20.0% |
| 5× | -25.5% |
| 0.5× (-50%) | -5.7% |
| 0.25× (-75%) | -20.0% |

**关键观察**:
1. **IL 跟价格变化方向无关**(涨跌都亏)
2. **IL 是非线性的**(变化越大,IL 急剧恶化)
3. **只有当价格回到初始时,IL 才回到 0**(所以叫"无常损失")

### 2.3 IL 跟手续费收入的赛跑

LP 的真实收益 = 手续费收入 - IL - gas - 对冲成本

**只有当手续费收入 > IL 时,做 LP 才划算**。

**经验法则**:
- 稳定币对(USDC/USDT):IL 极小,即使年化 5% 手续费也划算
- 主流币对(ETH/USDC):IL 中等,需要年化 20%+ 手续费
- 长尾币对(SHIB/USDT):IL 巨大,需要年化 100%+ 手续费

---

## 三、对冲机制

### 3.1 为什么要对冲

**不对冲的 LP**:
- 月化手续费收入:5%
- 月度 IL(中等波动):-3%
- 净收益:**+2%**

**对冲后的 LP**:
- 月化手续费收入:5%
- 月度 IL:-3%
- 对冲掉 IL:**+3%(对冲让 IL 变成 0)**
- 对冲成本:**-1%**(资金费率 + 滑点)
- 净收益:**+4%**

对冲让你**消除 IL 的方向性风险**,只剩下"手续费收入" - "对冲成本"的稳定差。

### 3.2 对冲的方法

**Uniswap V3 LP 对冲**:LP 在某个价格区间内,本质上等价于"持有期权 + 持有现货"。Delta(价格敏感度)随价格变化。

**对冲方法**:用永续合约对冲 LP 的 Delta。

**简化版**(初学):
- LP 池子:1 ETH + 3,000 USDC(假设)
- 你的 ETH 持仓 = 1 ETH(Delta = +1)
- 对冲:在永续合约上做空 1 ETH(Delta = -1)
- 组合 Delta = 0

**问题**:LP 的 ETH 持仓**不是固定的**——价格涨,你的 ETH 减少;价格跌,你的 ETH 增加。所以 **Delta 不断在变**。

**严格版**:
- 实时计算 LP 的当前 Delta
- 永续合约持仓动态调整
- 这叫 **Dynamic Hedging**(动态对冲)

### 3.3 Uniswap V3 的特殊处理

Uniswap V3 引入了"集中流动性"——LP 只在某个价格区间提供流动性。

**优点**:同样的资金,在窄区间提供流动性,**手续费收入是 V2 的 5-50 倍**

**缺点**:价格离开你的区间时,**完全停止赚手续费**,而且你的流动性变成 100% 的另一种资产

**例子**:
- 你在 ETH 价格 [$2,800, $3,200] 提供流动性
- 当前 ETH = $3,000,你的份额 50% ETH + 50% USDC
- ETH 涨到 $3,300:你的流动性变成 0% ETH + 100% USDC,**不再赚手续费**
- 直到 ETH 跌回 $3,200 以内,才重新激活

**对冲难度大幅增加**——必须实时调整对冲仓位,而且要考虑"出区间"的特殊情况。

---

## 四、策略具体做什么

### 4.1 选择池子

**适合做的池子**(主流 + 高交易量):
- Uniswap V3 ETH/USDC 0.05% (Ethereum / Arbitrum / Base)
- Uniswap V3 ETH/USDT 0.05%
- Uniswap V3 WBTC/USDC 0.05%
- PancakeSwap V3 BNB/USDC 0.05% (BSC)

**不做的池子**:
- 长尾币(IL 太大)
- 新协议(智能合约风险)
- TVL < $5M 的池子(流动性差,机器人少,套利机会少 → 你赚不到)

### 4.2 资金分配

**单池子最少 $5,000**(否则 gas 费占比太大):
- $2,500 ETH(LP 一边)
- $2,500 USDC(LP 一边)
- 永续合约对冲保证金:$1,000
- 总资金占用:**$6,000 per 池子**

**Phase 1 阶段**(资金 $30k+):
- 同时做 1-2 个池子
- 总占用 $10k-$15k

### 4.3 选择价格区间(V3 LP 关键)

```python
def select_v3_range(current_price, volatility_30d, target_apr):
    """
    根据波动率选择价格区间

    窄区间:手续费 APR 高,但容易出区间
    宽区间:手续费 APR 低,但稳定
    """
    if target_apr > 50:  # 激进
        # 窄区间 ±2σ
        lower = current_price * (1 - 2 * volatility_30d / sqrt(12))
        upper = current_price * (1 + 2 * volatility_30d / sqrt(12))
    elif target_apr > 20:  # 中等
        lower = current_price * (1 - 4 * volatility_30d / sqrt(12))
        upper = current_price * (1 + 4 * volatility_30d / sqrt(12))
    else:  # 保守
        lower = current_price * 0.7
        upper = current_price * 1.3

    return lower, upper
```

---

## 五、配置文件示例

```yaml
strategy_type: dex_lp_hedged
instance_name: lp_hedged_main
enabled: false                    # 默认禁用,Phase 1 才启用

capital:
  allocated: 10000                # $10k(Phase 1)
  reserved_hedge_margin: 2500     # 留 25% 做对冲保证金

scanning:
  pools:
    - protocol: uniswap_v3
      chain: arbitrum
      pool: ETH/USDC
      fee_tier: 500                # 0.05%

    - protocol: uniswap_v3
      chain: base
      pool: ETH/USDC
      fee_tier: 500

    - protocol: pancake_v3
      chain: bsc
      pool: BNB/USDC
      fee_tier: 500

  # 池子筛选
  min_tvl_usd: 5_000_000
  min_24h_volume_usd: 10_000_000
  min_volume_to_tvl_ratio: 0.3    # 池子周转率(volume/TVL)

range_selection:
  strategy: balanced              # conservative / balanced / aggressive
  rebalance_when_out_of_range: true
  max_out_of_range_hours: 6       # 出区间 6 小时不回 → 重新设置区间

hedging:
  hedge_exchange: binance         # 用 Binance 永续对冲
  hedge_check_interval_seconds: 60
  rehedge_threshold_pct: 5        # Delta 偏离 5% 重新对冲
  max_hedge_leverage: 3

risk:
  max_il_pct: 8                   # IL 超 8% 强制平仓
  daily_loss_limit_pct: 3
  smart_contract_risk_check: true # 检查合约审计状态

execution:
  gas_price_strategy: standard    # standard / fast / urgent
  max_gas_per_action_usd: 30      # 单次操作最高 gas $30
  use_mev_protection: true        # 用 Flashbots Protect

notifications:
  on_position_opened: [telegram, toast]
  on_out_of_range: [telegram, discord]
  on_il_warning: [telegram, discord]
  on_hedge_rebalance: [toast]
  on_smart_contract_alert: [telegram, discord, email]
```

---

## 六、风险与亏损场景

### 6.1 IL 超预期(中频)

**场景**:你在 ETH/USDC 池子里,ETH 突然 1 周内涨 50%。IL 达到 -2%,加上手续费收入只有 +1% → 净亏 1%。

**系统保护**:
- `max_il_pct: 8` — IL 超 8% 强制平仓
- 动态对冲(理论上对冲掉了)

**防不住的部分**:对冲不能完美 100%,残余 IL 0.5%-1% 是正常的

### 6.2 智能合约被黑(罕见但致命)

**真实案例**:
- 2024 年 8 月 Penpie 被盗 $27M
- 2024 年 5 月 Sonne Finance 被盗 $20M
- 2023 年 7 月 Curve 上某些池子被盗 $73M

**亏损方式**:
- 你池子里的资金可能**全部归零**
- 通常不可恢复

**系统保护**:
- 只用顶级协议(Uniswap、PancakeSwap、Curve)
- 监控协议安全公告
- 单池子资金不超过总资金 30%

**防不住的部分**:即使顶级协议也可能有 0-day 漏洞

### 6.3 出区间不赚钱

**场景**:V3 LP,价格出区间 24 小时未回 → 你完全停止赚手续费,但 IL 仍在累积

**系统保护**:
- `max_out_of_range_hours: 6` — 6 小时不回 → 重新设置区间
- 重新设置区间需要付 gas 费($10-$50)

### 6.4 Gas 费爆炸

**场景**:你在 Ethereum 主网做 LP,某 NFT mint 让 gas 涨到 200 gwei → 单次 rebalance 操作要 $100+ gas

**系统保护**:
- `max_gas_per_action_usd: 30` — 超阈值不操作
- 优先用 L2(Arbitrum / Base / BSC)

### 6.5 风险总结

| 风险 | 频率 | 单次损失 | 防御 |
|---|---|---|---|
| IL 超预期 | 中 | 1%-3% | 70%(对冲) |
| 智能合约被黑 | 罕见但致命 | 100% | 50%(只用顶级) |
| 出区间停赚 | 高频(每周) | 仅机会成本 | 90%(重新设置) |
| Gas 费爆炸 | 中 | 操作费用 | 95%(L2 + 阈值) |
| 对冲失败(永续问题) | 低 | 浮亏暴露 | 80%(监控) |
| MEV 攻击 | 高频(无防护时) | 0.3%-1%/笔 | 95%(Flashbots) |

---

## 七、回测和上线

### 7.1 回测的特殊难度

**问题**:DEX LP 没有完整的"成交价历史"——你能查到的是"价格变化",但不知道"在这个价格变化中,你能赚多少手续费"

**解决**:
- 用历史 swap volume + 你的份额估算手续费
- 用价格序列计算 IL
- 模拟对冲成本

### 7.2 通过标准

| 指标 | 通过标准 |
|---|---|
| **Sharpe** | > 1.0 |
| **Max DD** | < 8% |
| **平均月化** | > 1% |
| **对冲有效率** | > 80%(IL 被对冲掉的比例) |

### 7.3 实盘阶段

**特别保守**(因为风险高):

```
Stage 1:$2,000 实盘 / 1 个月
  - 仅 1 个池子(Arbitrum ETH/USDC)
  - 验证基础流程

Stage 2:$5,000 实盘 / 1 个月
  - 加对冲

Stage 3:$10,000 持续(Phase 1 目标)
  - 同时 1-2 个池子
```

---

## 八、与其他策略的协同

### 8.1 跟资金费率套利:轻微冲突

对冲腿在 Binance 做空 ETH 永续 → 资金费率套利可能也想做空 ETH 永续 → 资源冲突

**协同方案**:
- 资金费率套利优先,LP 策略让位
- 或 LP 策略用 Bybit 而不是 Binance 对冲

### 8.2 跟 CEX-DEX 套利:协同

CEX-DEX 套利监控的就是 Uniswap 的价格 → 你做 LP 时,CEX-DEX 套利可能频繁触发(因为有人来你的池子套利,你赚手续费)

**实际**:做 LP 让 CEX-DEX 套利更频繁,**对你有利**(手续费多)。

---

## 九、技术实现要点

### 9.1 关键模块

```
DexPoolMonitor             监控池子状态(TVL、volume、价格)
ILCalculator               实时计算 IL
DeltaCalculator            计算 LP 的当前 Delta
DynamicHedger              动态对冲调整
RangeManager               V3 价格区间管理
SmartContractRiskMonitor   智能合约风险监控
FlashbotsExecutor          MEV 保护的交易执行
```

### 9.2 链上交互

复用第 7 章的 DEX 适配器,但要扩展:
- 调用 NonfungiblePositionManager(Uniswap V3 的 LP 合约)
- 监听 Swap、Mint、Burn 事件
- 估算 gas + 设置 priority fee

---

## 总结

5 句话:

1. **本质**:做 LP 赚手续费 + 用永续对冲 IL
2. **关键概念**:IL(无常损失)是数学结构性的,不对冲就是亏
3. **资金门槛**:$10k 起步,Phase 1+ 才考虑
4. **最大风险**:智能合约风险(可能 100% 损失)
5. **位置**:Phase 1 后期补充策略,$10k 配置,月化 1%-3%

---

## 你的下一步

到这里,**5 份新策略文档全部写完**。我接下来要做:

1. **更新第 1 章**,加一节"未来策略候选 + 永久排除清单"
2. **更新 PDF**,加入 13-17 共 5 个新章节
3. **复制所有新文件到输出目录**
