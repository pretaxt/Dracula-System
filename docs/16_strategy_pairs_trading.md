# 16 · 永续合约对冲套利(配对交易)· 完整设计

> **策略类型**:`pairs_trading`
> **优先级**:🟢 P1(Phase 1 阶段最值得做的新策略)
> **预期月化收益**:0.5% - 2.0%
> **最大单月回撤**:-3%
> **资金需求**:$5,000 起;实战可扩展至 $200k+
> **策略复杂度**:⭐⭐⭐⭐(高)

---

## 序言:为什么这是最值得做的新策略

在你刚才让我加的 5 个策略里,**这一个最值得认真做**,理由:

1. **真正不同的策略**:跟你现有 5 个完全不同的收益来源(其他都是 Delta 中性的"白嫖费率"型,这个是统计套利型)
2. **资金量适配**:$30k+(你的 Phase 1 资金量)正好
3. **学习价值高**:做完这个,你对量化交易的理解上一个台阶
4. **稳定性好**:经过 30 年验证的策略(从传统股票市场就有)

但我必须老实告诉你:

⚠️ **这是这一批新策略中最容易过拟合、最容易自欺欺人的策略**。回测显示 Sharpe 3.0,实盘可能 0.3。**严格按本章的回测要求来**,不要跳步骤。

---

## 一、什么是配对交易

### 1.1 核心思想

**找两个币种,它们的价格"应该"高度相关**(比如 ETH 和 BNB 都是 L1 平台币),但**短期偶尔会偏离这个相关性**。

```
正常情况:
  ETH/BNB 比率 = 5.5(ETH 价格 / BNB 价格)
  这个比率 30 天波动范围:5.3 - 5.7

异常时刻:
  某新闻让 BNB 短期暴涨,但 ETH 没动
  ETH/BNB 比率瞬间跌到 5.0
  显著低于 30 天均值

操作:
  做多 ETH(便宜)
  做空 BNB(贵)
  等比率回归到 5.5 时平仓获利
```

### 1.2 跟资金费率套利的本质区别

| 维度 | 资金费率套利 | 配对交易 |
|---|---|---|
| 收益来源 | 资金费率(确定性) | 价差回归(统计性) |
| 风险 | 极低(Delta 0,市场无关) | **中等(可能不回归)** |
| 持仓时长 | 几天-几周 | 几小时-几天 |
| 入场逻辑 | "费率高就做" | "价差偏离均值就做" |
| 是否需要预测 | 不需要(只看费率) | **需要(预测价差会回归)** |

**关键区别**:配对交易**带有方向性赌博成分**——你赌"两个币的相对价值会回归到历史均值"。

如果两个币的关系**结构性变化**(比如其中一个被监管打击),价差永远不回归,你就被困住了。

---

## 二、币对选择(关键!)

### 2.1 找"应该相关"的币对

**好的配对**(高相关 + 同质化):

```
1. 同类 L1 公链:
   - ETH ↔ BNB(都是大平台)
   - SOL ↔ AVAX(都是高 TPS 链)
   - ADA ↔ DOT(都是学术型链)

2. 同类应用代币:
   - UNI ↔ SUSHI(都是 DEX)
   - AAVE ↔ COMP(都是借贷)
   - LDO ↔ RPL(都是 LST)

3. 同类 L2:
   - ARB ↔ OP(都是以太坊 L2)
   - MATIC ↔ MNT(扩容方案)

4. Meme 币:
   - DOGE ↔ SHIB(老牌 meme)
   - PEPE ↔ FLOKI(新一代 meme)

5. 中心化交易所代币:
   - BNB ↔ OKB
   - BNB ↔ HT(已下架)
```

**坏的配对**(看起来相关但不可靠):
- BTC ↔ Gold(完全不同的市场)
- ETH ↔ ETC(虽然兄弟链,但价值差太大)
- BTC ↔ MSTR 股票(传统市场和加密市场)

### 2.2 量化"相关性"

不是凭感觉选,要用数据验证:

```python
import numpy as np
from scipy import stats

def calculate_correlation(symbol_a: str, symbol_b: str, days: int = 90) -> dict:
    """计算两个币种的相关性指标"""
    prices_a = fetch_historical_prices(symbol_a, days, interval="1h")
    prices_b = fetch_historical_prices(symbol_b, days, interval="1h")

    # 1. 收益率序列(不是价格序列!)
    returns_a = np.diff(np.log(prices_a))
    returns_b = np.diff(np.log(prices_b))

    # 2. 皮尔逊相关系数(收益率)
    pearson, _ = stats.pearsonr(returns_a, returns_b)

    # 3. 斯皮尔曼相关系数(更稳健)
    spearman, _ = stats.spearmanr(returns_a, returns_b)

    # 4. 协整性测试(Engle-Granger test)
    from statsmodels.tsa.stattools import coint
    coint_score, p_value, _ = coint(prices_a, prices_b)

    return {
        "pearson_correlation": pearson,
        "spearman_correlation": spearman,
        "cointegration_p_value": p_value,
        "is_cointegrated": p_value < 0.05,
    }
```

**进入候选池的标准**:
- ✅ Pearson 收益率相关性 **> 0.7**
- ✅ Spearman 相关性 **> 0.65**
- ✅ 协整性 p-value **< 0.05**
- ✅ 90 天内相关性**稳定**(不是某段时间高某段时间低)

### 2.3 价差(Spread)计算

不能用简单的"价格差",要用**价格比率的标准化形式**:

```python
def calculate_spread(prices_a: pd.Series, prices_b: pd.Series) -> pd.Series:
    """计算价差"""

    # 方法 1:简单比率
    ratio = prices_a / prices_b

    # 方法 2:对数差(更稳健)
    log_spread = np.log(prices_a) - np.log(prices_b)

    # 方法 3:回归残差(最专业)
    # 用线性回归找 "best fit": prices_a = beta * prices_b + alpha
    from sklearn.linear_model import LinearRegression
    model = LinearRegression().fit(prices_b.values.reshape(-1, 1), prices_a.values)
    beta = model.coef_[0]
    alpha = model.intercept_
    residual_spread = prices_a - (beta * prices_b + alpha)

    return residual_spread  # 用方法 3
```

### 2.4 Z-Score 触发机制

```python
def calculate_zscore(spread: pd.Series, window: int = 30 * 24) -> float:
    """计算价差的 Z-score(标准化偏离)"""
    rolling_mean = spread.rolling(window).mean()
    rolling_std = spread.rolling(window).std()
    zscore = (spread.iloc[-1] - rolling_mean.iloc[-1]) / rolling_std.iloc[-1]
    return zscore

# 触发逻辑:
# zscore > +2.0  →  价差远高于均值,做空价差(空 A 多 B)
# zscore < -2.0  →  价差远低于均值,做多价差(多 A 空 B)
# zscore 回到 0 附近(±0.5)→ 平仓
```

**关键参数**:
- `window`:用 30 天的小时数据计算移动均值/标准差(720 小时)
- `entry_threshold`:|Z-score| > 2.0 进场
- `exit_threshold`:|Z-score| < 0.5 平仓
- `stop_loss_threshold`:|Z-score| > 3.5 强制止损(说明配对失效)

---

## 三、数学模型

### 3.1 单仓位的预期收益

```
进场时 z-score = -2.5(显著偏离)
预期回归到 z = 0
预期价差变化 = 2.5 × σ(spread)

毛收益(per $1000 仓位):
  if 历史 σ = 2% → 价差回归 = 5% → 毛收益 5%

成本(双向):
  手续费(永续):4 × 0.04% = 0.16%
  滑点:4 × 0.05% = 0.20%
  资金费率(估月化 5% × 0.5 月):2.5%
  总成本:约 2.86%

净收益:5% - 2.86% = 2.14%
```

**单仓位预期 1%-3%**,持仓 3-15 天。

### 3.2 资金费率成本是大头

**两边都做永续 → 都有资金费率**。

不像资金费率套利那样**收**资金费,这里很可能**双向都付**资金费(取决于市场情绪)。

**所以**:
- 进场前必须**预估资金费率成本**
- 持仓中持续监控,超阈值早平仓

### 3.3 仓位大小

```
建议每个仓位:总资金的 5%-10%
最多同时持仓:5-10 个不同配对
全部仓位占总资金:30%-50%(留一半做风控缓冲)

例如 $30k 资金:
  每个仓位 $1.5k-$3k
  同时 5-10 个仓位
  总占用 $15k-$25k
  保证金 + 缓冲 $5k-$15k
```

---

## 四、执行流程

### 4.1 双腿原子化(跟跨所价差套利相同)

两笔订单分别在永续 A 和永续 B 上。同所或跨所都可以(优先同所)。

**关键差异**:
- 这两腿**方向相反**(一多一空)
- 资金占用 = 两个仓位的保证金之和

### 4.2 持仓监控(关键)

```python
async def monitor_pairs_position(position):
    # 1. 实时计算 z-score
    current_spread = await calculate_current_spread(
        position.long_symbol, position.short_symbol
    )
    current_zscore = standardize(current_spread, position.lookback_window)

    # 2. 出场判断
    # 2a. 价差回归到正常
    if abs(current_zscore) < 0.5:
        await close_position(position, reason="mean_reverted")
        return

    # 2b. 价差进一步偏离(配对可能失效)
    if abs(current_zscore) > 3.5:
        await close_position(position, reason="pair_breakdown")
        await notify("WARN", "配对可能失效,强制止损")
        return

    # 3. 资金费率成本累计
    long_funding = await get_funding(position.long_symbol)
    short_funding = await get_funding(position.short_symbol)
    net_funding_cost = (long_funding - short_funding) * position.size_usd
    position.cumulative_funding += net_funding_cost

    if position.cumulative_funding > position.expected_profit * 0.5:
        # 资金费成本吃掉 50% 预期利润
        await close_position(position, reason="funding_cost_too_high")
        return

    # 4. 持仓时间
    if position.holding_days > 14:
        await close_position(position, reason="max_holding_time")
        return

    # 5. 相关性持续验证
    # 每 24 小时重算一次配对的协整性
    if position.holding_hours % 24 == 0:
        coint_p = await retest_cointegration(position.long_symbol, position.short_symbol)
        if coint_p > 0.10:
            # 协整性失效
            await close_position(position, reason="cointegration_lost")
```

---

## 五、出场策略

| 条件 | 阈值 | 理由 |
|---|---|---|
| **z-score 回归** | abs(z) < 0.5 | 主要出场信号 |
| **z-score 进一步偏离** | abs(z) > 3.5 | 配对失效止损 |
| **资金费成本翻车** | > 50% expected_profit | 成本超支 |
| **持仓 14 天** | days > 14 | 时间止损 |
| **协整性失效** | re-test p > 0.10 | 配对结构变了 |

---

## 六、配置文件示例

```yaml
strategy_type: pairs_trading
instance_name: pairs_main
enabled: false                    # 默认禁用,Phase 1 才启用

capital:
  allocated: 10000                # Phase 1 阶段 $10k
  per_position_size: 1500         # 单仓 $1500

scanning:
  exchanges: [binance, bybit, okx]
  scan_interval_minutes: 15       # 15 分钟扫一次(配对交易不需要高频)

  # 配对池(预定义 + 系统验证)
  predefined_pairs:
    - {long: ETH/USDT, short: BNB/USDT, category: l1}
    - {long: SOL/USDT, short: AVAX/USDT, category: l1}
    - {long: ARB/USDT, short: OP/USDT, category: l2}
    - {long: UNI/USDT, short: SUSHI/USDT, category: dex}
    - {long: AAVE/USDT, short: COMP/USDT, category: lending}
    - {long: DOGE/USDT, short: SHIB/USDT, category: meme}
    - {long: LDO/USDT, short: RPL/USDT, category: lst}

  # 自动发现(可选)
  auto_discover_pairs: false      # Phase 1 暂不开

cointegration:
  lookback_days: 90               # 90 天历史数据
  recalibration_hours: 24         # 24 小时重算一次
  min_correlation: 0.70
  max_p_value: 0.05

entry_rules:
  zscore_threshold: 2.0           # |z| > 2.0 进场
  zscore_confirm_period_minutes: 30   # 持续 30 分钟才进场(避免噪音)
  max_position_count: 5
  max_per_position: 1500

  # 流动性要求
  min_24h_volume_usd: 50_000_000  # 两边都要有

exit_rules:
  zscore_exit_threshold: 0.5
  zscore_stop_loss: 3.5
  funding_cost_threshold_pct: 50
  max_holding_days: 14
  cointegration_retest_p_value: 0.10

risk:
  max_leverage: 3
  delta_neutral_tolerance_pct: 5  # 配对交易允许更大的 Delta 偏离
  max_drawdown_per_position_pct: 8

execution:
  order_type: limit
  fill_timeout_seconds: 5
  rollback_on_one_leg_fail: true

notifications:
  on_pair_signal: [toast]
  on_entry: [telegram, toast]
  on_exit: [telegram, toast]
  on_pair_breakdown: [telegram, discord, email]   # 配对失效是大事
  on_recalibration_warning: [telegram]
```

---

## 七、风险与亏损场景

### 7.1 配对失效(最严重)

**场景**:你做"ETH 多 / BNB 空",历史相关性 0.85。突然 BNB 因为某监管事件单独暴跌 20%,ETH 没动 → 你做空的 BNB 暴跌赚钱,但远不够弥补做多 ETH 的浮亏(还要承受永续合约清算风险)

**亏损方式**:
- z-score 不会回归,反而扩大
- 持仓亏损 5%-15%
- 极端情况下永续合约爆仓

**系统保护**:
- `zscore_stop_loss: 3.5` — 强制止损
- `cointegration_retest_p_value: 0.10` — 配对失效自动平仓
- `max_leverage: 3` — 防止爆仓

**防不住的部分**:
- 监管事件的瞬间冲击,系统响应不过来
- 配对失效可能是**长期结构性**变化,平仓 = 锁定亏损

**真实案例**:
- LUNA 崩盘前,LUNA-AVAX 配对突然失效,损失 50%+
- FTT 倒闭前,FTT-BNB 配对崩盘
- 2024 年 ETF 通过后,BTC 与其他山寨币的配对关系大幅改变

### 7.2 资金费率成本侵蚀利润

跟其他双永续策略相同,这里不重复。

### 7.3 过拟合陷阱(回测时最严重)

**场景**:你回测 90 个配对,选出 Sharpe > 2 的 10 个。实盘跑这 10 个,Sharpe 0.3。

**原因**:**多重检验偏差**——你测试越多配对,越可能找到"运气好"的配对。

**防御**:
- **样本外测试**:用 70% 数据找配对,30% 数据验证
- **Walk-forward 测试**:每个月重新筛配对,看是否稳定
- **配对的"基本面理由"**:不只是统计相关,还要有商业逻辑(比如都是 L1 公链)

### 7.4 风险总结

| 风险 | 频率 | 单次损失 | 防御 |
|---|---|---|---|
| 配对失效 | 5%-15%(每个仓位) | 5%-15% | 70%(止损 + 重新检验) |
| 资金费成本 | 持续 | 0.5%-3% | 80%(预算监控) |
| 过拟合(回测层面) | 100%(必然存在) | 实盘表现远低于回测 | 60%(严格样本外) |
| 永续清算 | 罕见 | 50%-100% | 95%(3 倍杠杆上限) |

---

## 八、回测要求(关键!)

⚠️ **这个策略的回测最容易作弊**。严格按下面来,**不要心存侥幸**。

### 8.1 数据需求

- **历史窗口**:**至少 24 个月**(覆盖牛熊)
- **数据频率**:1 小时 K 线
- **必须包含**:312、519、LUNA 崩盘、FTX 倒闭、SVB 危机

### 8.2 回测设计

```python
def proper_backtest(pair_pool: List[Pair]):
    # 1. 严格的样本划分
    in_sample = data["2023-01-01":"2024-06-30"]   # 18 个月
    out_sample = data["2024-07-01":"2024-12-31"]  # 6 个月

    # 2. 在 in-sample 上筛选配对
    selected_pairs = []
    for pair in pair_pool:
        coint = test_cointegration(pair, in_sample)
        if coint.is_cointegrated:
            selected_pairs.append(pair)

    # 3. 在 out-sample 上回测(不动参数)
    results = []
    for pair in selected_pairs:
        result = run_strategy(pair, out_sample, params=DEFAULT_PARAMS)
        results.append(result)

    return aggregate(results)

# 4. Walk-forward 验证(更严格)
def walk_forward_backtest():
    windows = [
        ("2023-01", "2023-06", "2023-07"),  # train, test 1 month
        ("2023-02", "2023-07", "2023-08"),
        # ... 每月滑动
    ]
    all_oos_returns = []
    for train_start, train_end, test_month in windows:
        pairs = select_pairs(data[train_start:train_end])
        oos_return = run_strategy(pairs, data[test_month])
        all_oos_returns.append(oos_return)

    return {
        "monthly_returns": all_oos_returns,
        "sharpe": calculate_sharpe(all_oos_returns),
        "consistency": fraction_positive_months(all_oos_returns),
    }
```

### 8.3 通过标准(严格)

| 指标 | 通过标准 | 说明 |
|---|---|---|
| **In-sample Sharpe** | > 2.0 | 容易达到 |
| **Out-of-sample Sharpe** | > 1.0 | **关键!** |
| **In/Out Sharpe ratio** | > 0.5 | 衡量过拟合 |
| **Max DD** | < 8% | |
| **Walk-forward Sharpe** | > 0.8 | 最严格 |
| **正收益月份比例** | > 60% | 一致性 |

**关键判断**:**out-of-sample Sharpe > 1.0 才算策略真的 work**。如果 in-sample 3.0 但 out-of-sample 0.3,说明严重过拟合,**不要上实盘**。

---

## 九、Paper Trading + 实盘

### 9.1 Paper Trading

- **运行时长**:**至少 1 个月**(因为单仓持有 3-14 天,需要时间)
- **必须经历**:至少 5 个完整的"开仓 → 持有 → 平仓"周期
- **必须经历**:至少 1 次"配对失效"的应对

### 9.2 实盘阶段

```
Stage 1:$1,500 实盘 / 1 个月
  - 仅 1 个配对(ETH/BNB,最稳的)
  - 验证执行链路 + 真实资金费成本

Stage 2:$3,000 实盘 / 1 个月
  - 加 SOL/AVAX

Stage 3:$5,000 实盘 / 持续
  - 同时 3 个配对

Stage 4:$10,000+(Phase 1 后期)
  - 同时 5 个配对
```

每阶段要**完整经历至少 5 个交易周期**才升级。

---

## 十、与其他策略的协同

### 10.1 跟资金费率套利:**严重冲突**

资金费率套利在 ETH/USDT 永续上做空,配对交易可能在 ETH/USDT 永续上做多 → **互相对冲**

**协同方案**:
- 资源锁机制(`{exchange}:{symbol}:perp:{side}`)
- 资金费率套利优先级更高
- 配对交易自动跳过冲突币种

### 10.2 跟基差套利:轻微冲突

类似上面的处理。

---

## 十一、技术实现要点

### 十一.1 关键模块

```
PairsScreener              扫描所有可能配对,筛选高相关
CointegrationTester        协整性测试(每 24 小时重测)
SpreadCalculator           价差计算(基于回归残差)
ZScoreMonitor              Z-score 实时监控
PairsExecutor              双腿对冲执行
PairBreakdownDetector      配对失效检测
```

### 十一.2 数据库

新增表:

```sql
CREATE TABLE pairs_definitions (
    id BIGSERIAL PRIMARY KEY,
    long_symbol VARCHAR(30) NOT NULL,
    short_symbol VARCHAR(30) NOT NULL,
    category VARCHAR(50),
    pearson_correlation DECIMAL(10, 6),
    cointegration_p_value DECIMAL(10, 6),
    last_recalibrated_at TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE (long_symbol, short_symbol)
);

CREATE TABLE pairs_zscore_history (
    time TIMESTAMPTZ NOT NULL,
    pair_id BIGINT REFERENCES pairs_definitions(id),
    spread_value DECIMAL(20, 8),
    zscore DECIMAL(10, 4),
    rolling_mean DECIMAL(20, 8),
    rolling_std DECIMAL(20, 8)
);
SELECT create_hypertable('pairs_zscore_history', 'time');
```

---

## 总结

5 句话:

1. **本质**:找历史相关性高的两个币,套利"短期偏离"的回归
2. **关键**:严格的协整性测试 + 样本外验证(防过拟合)
3. **风险**:配对失效是真实存在的(LUNA、FTT 案例)
4. **资金**:Phase 1 阶段 $5k-$10k,$30k+ 时是主要策略
5. **位置**:Phase 1 上线,真正不同于其他 Delta 中性策略的统计套利

---

## 你的下一步

接下来最后一章:第 17 章 DEX LP + 对冲策略
