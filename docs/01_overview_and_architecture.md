# Dracula-System 多策略量化套利系统 · 完整设计文档

> **版本**:v1.1 (Review 后更新)
> **日期**:2026-05-06
> **状态**:✅ 已通过用户 review,设计阶段冻结
> **预计开发周期**:8-12 周
> **目标读者**:项目主理人(老虎)+ AI 协作开发者(Claude)

---

## 🔄 Review v1.1 更新摘要

> **2026-05-06 用户(老虎)review 后**,以下决策已冻结:

### 关键变更

| # | 项目 | v1.0 设计 | v1.1 决策 |
|---|---|---|---|
| 1 | **策略总数** | 17 个候选 | **12 个保留 + 5 个永久排除** |
| 2 | **风控熔断** | 单层(触发即全平) | **三层熔断**(单策略/账户/强平) |
| 3 | **杠杆** | 统一 3x | **分层杠杆**(Tier A 5x / Tier B 3x / Tier C 禁) |
| 4 | **多用户** | Phase 2 一次性做 | **拆两步**(Phase 1 双用户家人版 / Phase 2 多用户朋友版) |

### 12 个保留策略

```
P0 主力(立即做):#1 资金费率 / #4 期现 / #13 三角

P1+ 候选(资金/时间到位再启用):
  #2 跨所基差 / #5 CEX-DEX(监控) / #6 期权波动率(P0 禁用)
  #7 网格 / #9 做市 / #10 趋势 / #12 因子
  #14 稳定币利率 / #16 配对交易
```

### 5 个永久排除

```
#3 跨所价差套利 · #8 IDO/IEO · #11 ETF 套利 · #15 跨链桥 · #17 MEV
```

### 详细 review 记录

- [docs/REVIEW_RESULT.md](./REVIEW_RESULT.md) — 12 项决策最终结果
- [docs/REVIEW_CHECKLIST_12_DECISIONS.md](./REVIEW_CHECKLIST_12_DECISIONS.md) — 12 项决策详细说明
- [docs/STRATEGIES_17_COMPARISON.md](./STRATEGIES_17_COMPARISON.md) — 17 策略对比

---

## ⚠️ 必读:项目现实预期声明

在你阅读完整设计之前,必须先读这一节。如果读完这一节你觉得难以接受,那么我们应该**回到方案 A 而不是 B**。

### 这个系统能做到什么

1. **跨 9 家交易所(5 CEX + 2 DEX 永续 + 2 DEX 现货)的统一监控与套利执行**
2. **5 个独立策略并行运行,资金独立分配,风控独立追踪**
3. **资金费率套利**:可期望的年化收益 8%-25%(熊市 5%-10%,牛市 30%+)
4. **跨所基差套利(永续 vs 季度)**:可期望年化 5%-15%,容量大但 edge 小
5. **期现套利(现货 vs 永续)**:可期望年化 5%-20%,本质上是资金费率套利的另一种形式
6. **CEX-DEX 价差套利**:仅做监控提示,不自动执行(原因后述)
7. **期权波动率策略**:可监控分析,但 **$5k 资金下能做的事情非常有限**

### 这个系统**做不到**什么

我必须诚实告诉你:

1. **做不到"包赚不赔"**。每一个策略都有可亏损的场景,我会在每个策略章节里讲明白
2. **做不到"自动赚钱"**。系统提供能力,但策略选择、资金分配、何时停机这些决策仍然要人介入
3. **做不到"打败专业团队"**。Jump、Wintermute、Jane Street 这些团队比你早 5 年开始做这件事。我们能做的是吃他们看不上的小机会
4. **小资金($5k)做不了真正的期权波动率交易**。Deribit BTC 期权一手保证金 $5k+,根本无法构建 Greeks 中性组合
5. **做不到"无风险套利"**。所有所谓"套利",实际上都有交易所风险、清算风险、操作风险、黑天鹅风险

### 资金分配预期(基于 $5k Phase 0 起步)

| 策略 | 建议初始资金 | 预期月收益 | 最大单月回撤 | 备注 |
|---|---|---|---|---|
| 资金费率套利 | $2,000 | 1.0%-2.0% | -3% | 主力策略,最稳 |
| 跨所基差套利 | $1,000 | 0.5%-1.5% | -2% | 容量大但 edge 极小 |
| 期现套利 | $1,000 | 0.8%-1.5% | -2% | 跟资金费率本质重合 |
| CEX-DEX 监控 | $0(只看不做) | - | - | 提示,不自动执行 |
| 期权波动率 | $500-1,000 | -10% 到 +20% | -30% | **方向性赌博,不是套利** |
| **预留保证金** | $500 | - | - | 应对极端行情补保证金 |

**整体预期**:Phase 0 三个月,系统化跑通流程后,**月化收益 1%-3% 是现实区间**,不是 10%+。如果有人告诉你年化 100%+ 是稳的,那是骗子或者还没爆仓的运气好的人。

---

## 一、系统总体架构

### 1.1 设计哲学

四条铁律,贯穿整个系统设计:

1. **可插拔(Pluggable)**:策略、交易所、通知渠道都是插件,新增不改核心
2. **事件驱动(Event-Driven)**:所有跨模块通信走事件总线,不直接相互调用
3. **风控前置(Risk-First)**:任何下单前必须经过风控总线审批,无例外
4. **状态可观测(Observable)**:每个模块的状态都能被仪表盘看到,所有动作都有日志

### 1.2 分层架构图

```
┌──────────────────────────────────────────────────────────────────┐
│                      PRESENTATION LAYER                          │
│  ┌────────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│  │  Web Dashboard │  │  Telegram Bot│  │  Discord/Email      │  │
│  │  (React/Next)  │  │  (双向交互)  │  │  (单向通知)         │  │
│  └────────┬───────┘  └──────┬───────┘  └──────────┬──────────┘  │
│           │ WebSocket       │ Long polling        │ Webhook      │
└───────────┼─────────────────┼─────────────────────┼──────────────┘
            │                 │                     │
┌───────────▼─────────────────▼─────────────────────▼──────────────┐
│                      APPLICATION LAYER                           │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ FastAPI REST/WebSocket Server                                ││
│  │  - /api/strategies      策略管理                              ││
│  │  - /api/positions       持仓查询                              ││
│  │  - /api/opportunities   机会扫描                              ││
│  │  - /api/risk            风控状态                              ││
│  │  - /api/notifications   通知历史                              ││
│  │  - /ws/realtime         实时推送                              ││
│  └─────────────────────────────────────────────────────────────┘│
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                    STRATEGY LAYER (可插拔)                       │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────┐│
│  │ Funding │ │  Basis  │ │SpotPerp │ │CEX-DEX  │ │ Options Vol ││
│  │  Rate   │ │   Arb   │ │   Arb   │ │   Arb   │ │     Arb     ││
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └──────┬──────┘│
│       │           │           │           │             │        │
│       └───────────┴───────────┴───────────┴─────────────┘        │
│                            │                                     │
│       每个策略实现 StrategyBase 接口:                             │
│       - scan_opportunities()                                     │
│       - validate_entry(opp)                                      │
│       - generate_orders(opp)                                     │
│       - monitor_position(pos)                                    │
│       - generate_exit_signal(pos)                                │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Events
┌────────────────────────────▼─────────────────────────────────────┐
│                       CORE LAYER (核心总线)                      │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ Event Bus (asyncio + Redis Pub/Sub)                          ││
│  │  Events: opportunity_found, order_request, position_opened,  ││
│  │          position_closed, risk_alert, system_halt            ││
│  └─────────────────────────────────────────────────────────────┘│
│  ┌──────────────────┐ ┌────────────────┐ ┌────────────────────┐ │
│  │  Risk Engine     │ │  Position Mgr  │ │  Execution Engine  │ │
│  │  - 多层风控审批  │ │  - 跨策略仓位  │ │  - 智能订单路由    │ │
│  │  - 熔断逻辑      │ │  - 资金分配    │ │  - 失败回滚        │ │
│  │  - 红线监控      │ │  - 互斥锁定    │ │  - 滑点保护        │ │
│  └──────────────────┘ └────────────────┘ └────────────────────┘ │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│                  EXCHANGE ADAPTER LAYER                          │
│  ┌───────────────────────┐  ┌────────────────────────────────┐  │
│  │  CEX (CCXT 统一封装)  │  │  DEX (各自 SDK)                │  │
│  │  - Binance / Bybit    │  │  Perp: Hyperliquid / dYdX v4   │  │
│  │  - OKX / HTX / Bitget │  │  Spot: Uniswap / PancakeSwap   │  │
│  └───────────────────────┘  └────────────────────────────────┘  │
│  统一接口: ExchangeAdapter                                       │
│   - fetch_ticker / fetch_orderbook / fetch_funding               │
│   - place_order / cancel_order / fetch_position                  │
│   - subscribe_websocket                                          │
└────────────────────────────┬─────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────┐
│                    PERSISTENCE LAYER                             │
│  ┌─────────────────┐ ┌─────────────────┐ ┌────────────────────┐ │
│  │ PostgreSQL      │ │ TimescaleDB     │ │ Redis              │ │
│  │ - 持仓 / 订单   │ │ - 历史价格      │ │ - 实时缓存         │ │
│  │ - 策略配置      │ │ - 资金费率历史  │ │ - 锁 / 限流        │ │
│  │ - 通知记录      │ │ - PnL 时序      │ │ - Pub/Sub          │ │
│  └─────────────────┘ └─────────────────┘ └────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### 1.3 关键架构决策

#### 决策 1:为什么用事件总线而不是直接调用?

**理由**:策略 → 风控 → 执行这条链路如果用直接调用,会变成:
```python
# 反例:紧耦合
class FundingStrategy:
    def on_opportunity(self, opp):
        if self.risk.check(opp):     # 直接调用风控
            self.exec.place(opp)     # 直接调用执行
```

这样设计的问题:
- 策略需要知道风控和执行的具体实现
- 加新策略要改老代码
- 测试时 mock 一堆依赖
- 多策略并发时,锁的粒度难以控制

**正例**(事件驱动):
```python
class FundingStrategy:
    def on_opportunity(self, opp):
        self.bus.publish("opportunity_found", opp)
        # 策略不需要知道下游有谁订阅
```

风控、执行、通知都订阅 `opportunity_found` 事件,各自处理各自的事。新增策略零改动。

#### 决策 2:为什么用 Redis 而不是只用 asyncio?

**asyncio 内存事件总线**:同进程内的事件,延迟最低
**Redis Pub/Sub**:跨进程、跨机器,可持久化

**双层设计**:
- 同进程内的策略通信用 asyncio 事件
- 跨进程的(比如 web 后端 ↔ 策略引擎)用 Redis
- 关键事件(订单成交、风控触发)同时进数据库和 Redis,保证不丢

#### 决策 3:为什么用 PostgreSQL + TimescaleDB?

- **PostgreSQL**:事务型数据(持仓、订单、配置),需要 ACID
- **TimescaleDB**:时序数据(价格、资金费、PnL),压缩比高,聚合查询快
- TimescaleDB 是 PostgreSQL 的扩展,可以在同一个数据库里用,部署成本低

#### 决策 4:策略实例 vs 策略类型

**重要概念**:同一个"策略类型"可以跑多个"策略实例"。

例如**资金费率套利**这个策略类型,可以同时跑:
- 实例 A:`funding_rate_conservative`,只接 Binance/Bybit,APR > 20% 才进
- 实例 B:`funding_rate_aggressive`,包含 Hyperliquid,APR > 10% 就进
- 实例 C:`funding_rate_meme`,只做 meme 币,APR > 30% 才进

每个实例有独立的:
- 配置文件
- 资金额度
- 持仓
- PnL 跟踪
- 风控参数

```yaml
# config/strategies/funding_rate_conservative.yaml
strategy_type: funding_rate_arb
instance_name: funding_rate_conservative
enabled: true
allocated_capital: 2000
exchanges: [binance, bybit, okx]
min_apr: 20.0
max_position_per_symbol: 500
max_concurrent_positions: 3
```

---

## 二、目录结构

```
Dracula-System/
├── README.md
├── docker-compose.yml
├── pyproject.toml
├── .env.example
│
├── config/
│   ├── config.yaml                   # 系统级(Tier 3,锁定)
│   ├── exchanges.yaml                # 交易所连接
│   ├── strategies/                   # 策略实例配置
│   │   ├── funding_rate_main.yaml
│   │   ├── basis_arb_main.yaml
│   │   ├── spot_perp_main.yaml
│   │   ├── cex_dex_monitor.yaml
│   │   └── options_vol_main.yaml
│   └── notifications.yaml
│
├── core/                             # 核心层(策略无关)
│   ├── strategy_base.py
│   ├── position_manager.py
│   ├── risk_engine.py
│   ├── execution_engine.py
│   ├── event_bus.py
│   ├── pnl_tracker.py
│   ├── scheduler.py
│   └── models/
│
├── strategies/                       # 策略层(可插拔)
│   ├── registry.py                   # 动态加载
│   ├── funding_rate_arb/
│   ├── perp_basis_arb/
│   ├── spot_perp_arb/
│   ├── cross_exchange_arb/
│   └── options_vol_arb/
│
├── exchanges/                        # 交易所适配层
│   ├── base.py
│   ├── cex/                          # CCXT 封装
│   │   ├── binance.py
│   │   ├── bybit.py
│   │   ├── okx.py
│   │   ├── htx.py
│   │   └── bitget.py
│   ├── dex_perp/
│   │   ├── hyperliquid.py
│   │   └── dydx.py
│   └── dex_spot/
│       ├── uniswap.py
│       └── pancakeswap.py
│
├── data/
├── api/                              # FastAPI 后端
├── notifications/
├── backtest/
├── frontend/                         # Next.js + TS
├── scripts/
├── tests/
└── docs/
```

**总计代码量预估**:
- Python 后端:8000-12000 行
- TypeScript 前端:3000-5000 行
- 配置 + 文档:2000-3000 行
- 测试:3000-5000 行
- **总计:约 16000-25000 行**

---

## 三、统一仓位管理(Position Manager)

这是整个系统**最关键也最容易出 bug 的模块**。

### 3.1 它要解决什么问题?

**问题 1**:多个策略可能同时想在同一个币种上建仓
- 资金费率套利想买 ETH 现货 + 卖 ETH 永续
- 期现套利也想买 ETH 现货 + 卖 ETH 永续
- 如果两个策略同时下单,会冲突

**问题 2**:每个策略需要知道自己分到了多少钱
- 总资金 $5000
- 资金费率策略分配 $2000
- 期现策略分配 $1000
- 必须严格隔离,一个策略爆仓不能影响另一个

**问题 3**:跨交易所的"资产"统一视图
- Binance 上有 1 BTC 现货
- Hyperliquid 上有 -1 BTC 永续空单
- 系统必须知道这两个组成了一个 Delta 中性对

### 3.2 核心数据模型

```python
# 持仓的最小单位:Leg(腿)
class Leg:
    leg_id: UUID
    exchange: str           # binance / hyperliquid / ...
    symbol: str             # BTC/USDT
    instrument_type: str    # spot / perpetual / futures / option
    side: str               # long / short
    size: Decimal
    entry_price: Decimal
    current_price: Decimal
    leverage: Decimal       # 1 表示现货
    margin: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    funding_paid: Decimal   # 资金费累计
    opened_at: datetime

# 一组 Leg 组成一个 Position(逻辑持仓)
class Position:
    position_id: UUID
    strategy_instance: str  # 哪个策略实例创建的
    strategy_type: str
    legs: List[Leg]         # 通常 2-4 条腿
    status: str             # pending / open / closing / closed
    delta_exposure: Decimal # 净 Delta(应接近 0)
    target_apr: Decimal
    actual_apr: Decimal
    opened_at: datetime
    closed_at: Optional[datetime]
    notes: str

# 策略实例的资金账户
class StrategyAccount:
    instance_name: str
    allocated_capital: Decimal     # 配置文件中分配的
    available_capital: Decimal     # 可用(未占用)
    positions: List[Position]
    total_pnl: Decimal
    daily_pnl: Decimal
    drawdown: Decimal
```

### 3.3 关键操作的并发控制

```python
class PositionManager:

    async def request_position(
        self,
        strategy: str,
        intended_legs: List[LegRequest]
    ) -> Position | RejectionReason:

        # 1. 全局锁
        async with self.global_lock:

            # 2. 检查策略账户余额
            account = self.accounts[strategy]
            required = sum(leg.notional for leg in intended_legs)
            if required > account.available_capital:
                return RejectionReason.INSUFFICIENT_CAPITAL

            # 3. 检查交易所资源是否被锁
            for leg in intended_legs:
                key = f"{leg.exchange}:{leg.symbol}:{leg.side}"
                if self.resource_locks.is_locked(key):
                    return RejectionReason.RESOURCE_CONTENTION

            # 4. 风控审批
            decision = await self.risk_engine.evaluate(
                strategy=strategy, legs=intended_legs, account=account
            )
            if not decision.approved:
                return decision.reason

            # 5. 锁定资源 + 预扣资金
            position = Position(strategy=strategy, legs_pending=intended_legs)
            for leg in intended_legs:
                self.resource_locks.acquire(
                    f"{leg.exchange}:{leg.symbol}:{leg.side}",
                    owner=position.id
                )
            account.available_capital -= required

            # 6. 提交执行
            await self.execution_engine.execute(position)

            return position
```

**关键设计点**:
- **资源锁**:`{exchange}:{symbol}:{side}` 粒度。同一交易所同一币种同一方向只能被一个策略持有
- **预扣资金**:风控批准后立即扣除,防止并发请求超额
- **失败回滚**:执行失败时回滚所有锁和资金

### 3.4 跨策略 PnL 归因

每个策略的 PnL 由几部分组成:
- **price_pnl**:价格变动产生的(中性策略应接近 0)
- **funding_pnl**:资金费率收入(资金费率套利的主要收入)
- **basis_pnl**:基差收敛 PnL(基差套利)
- **theta_pnl**:Theta 衰减(期权)
- **vega_pnl**:Vega(期权 IV 变化)
- **fee_paid**:手续费支出
- **slippage_loss**:滑点损失
- **net_pnl**:总和

**为什么要做归因**:跑了一个月发现亏钱,你需要知道是手续费吃掉的、滑点吃掉的、还是策略本身判断错了。否则不知道怎么调优。

---

## 四、风控总线(Risk Engine)

### 4.1 三层风控架构

```
策略发起请求
     │
     ▼
┌─────────────────────────────────────────┐
│ 第一层:策略级风控(Tier 1,自由调整)  │
│  - 单币种最大仓位、最大并发数            │
│  - 配置:strategies/{instance}.yaml      │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 第二层:账户级风控(Tier 2,延迟生效)  │
│  - 跨策略总仓位限制                      │
│  - 单交易所占比、单币种占比              │
│  - 配置:config.yaml(可调)             │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 第三层:系统级风控(Tier 3,锁定)      │
│  - 单日回撤、保证金率                    │
│  - 配置:config.yaml(改 yaml 重启)     │
└─────────────────────────────────────────┘
     │
     ▼
   通过 → 执行
   拒绝 → 通知 + 日志
```

### 4.2 实时监控指标

每秒级监控:

```
=== Tier 3 三层熔断(命根子)===
3a 单策略熔断:
  per_strategy_drawdown   触发 -3%(单策略浮亏)→ 停建仓
  
3b 账户熔断:
  daily_drawdown          触发 -3%(账户单日)→ 停建仓
  weekly_drawdown         触发 -8%(账户周)→ 停建仓
  
3c 强制平仓:
  daily_drawdown_force    触发 -5%(账户单日)→ 强平
  min_margin_ratio        触发 50%(保证金率)→ 强平
  api_error_count_5m      触发 3 次 → 强平
  ws_disconnect_seconds   触发 60 秒 → 强平

=== Tier 2(账户级)===
total_position_ratio
max_per_exchange_ratio
max_per_symbol_ratio

=== Tier 1(策略级)===
per_strategy_drawdown_warn   (-1%,只通知)
per_strategy_position_count
per_strategy_capital_used
```

### 4.3 熔断机制(Review v1.1 更新:三层熔断)

不是简单的"触发 → 全部平仓"。**根据严重程度分三级**:

```
═══ Tier 3a · 单策略熔断(轻度)═══
触发:单策略浮亏 ≥ -3%(占该策略分配资金)
动作:
  1. 该策略停止新建仓
  2. 现有仓位继续持有(不强平)
  3. WARN 级通知
  4. 浮亏回到 -1% 以内 → 自动恢复

═══ Tier 3b · 账户熔断(中度)═══
触发:总账户单日 -3%
动作:
  1. 全部策略停止新建仓
  2. 现有仓位继续持有(不强平)
  3. CRITICAL 级通知(全渠道)
  4. 必须修改 yaml + 重启系统才能恢复

═══ Tier 3c · 强制平仓(重度)═══
触发:总账户单日 -5% 或保证金率 < 50% 或 API 错误率超阈
动作:
  1. 立即强制平仓所有持仓(并发执行)
  2. 系统标记 HALT 状态
  3. 拒绝所有新订单
  4. CRITICAL 级通知(全渠道 + 多次重发)
  5. 必须修改 yaml + 重启系统才能恢复
```

**为什么分三层**:
- 旧设计("一触发就全平")过于一刀切,会:
  - 错过本可恢复的小波动
  - 频繁产生摩擦成本(平仓 + 重新建仓的手续费)
  - 单策略 bug 不应连累其他策略
- 新设计三级独立:
  - 3a 处理"单策略小问题"
  - 3b 处理"系统整体异常"
  - 3c 处理"必须立即止损"

**熔断的核心哲学没变**:
- Tier 3a 可以**自动恢复**(系统级"提示")
- Tier 3b/3c **不会自动恢复**,必须人工介入(故意的"摩擦"设计)

---

## 五、配置文件示例

### 5.1 系统级 `config/config.yaml`(Tier 3)

```yaml
system:
  total_capital: 5000
  base_currency: USDT

risk:
  # Tier 3a · 单策略熔断(锁定)
  per_strategy_drawdown_halt: -3.0    # 单策略浮亏 -3% → 停建仓
  per_strategy_recovery: -1.0         # 浮亏回到 -1% 内 → 自动恢复
  
  # Tier 3b · 账户熔断(锁定)
  daily_drawdown_halt: -3.0           # 账户单日 -3% → 停建仓
  weekly_drawdown_halt: -8.0          # 账户周 -8% → 停建仓
  
  # Tier 3c · 强制平仓(锁定)
  daily_drawdown_force: -5.0          # 账户单日 -5% → 强平
  min_margin_ratio: 50.0              # 保证金率 < 50% → 强平
  api_error_threshold_5m: 3
  ws_disconnect_threshold_seconds: 60

  on_force_close:
    notify_all_channels: true
    notify_repeat_count: 3            # 重发 3 次确保看到
    auto_resume: false                # 必须人工介入
  
  # Tier 2(可调,延迟生效)
  max_per_exchange_pct: 50.0
  max_per_symbol_pct: 20.0
  min_required_apr: 15.0

# 分层杠杆(Review v1.1 新增)
leverage_tiers:
  # Tier A 币种:最稳定,允许 5x
  tier_a:
    symbols: [BTC/USDT, ETH/USDT, SOL/USDT]
    max_leverage: 5
    force_close_pct: 18.0      # 币价跌 18% 强平(留 2% 缓冲)
  
  # Tier B 币种:流动性中等,3x
  tier_b:
    symbols: [BNB/USDT, XRP/USDT, DOGE/USDT, ADA/USDT, MATIC/USDT]
    max_leverage: 3
    force_close_pct: 25.0
  
  # Tier C 币种:流动性低 → 禁止
  tier_c_blacklist:
    enabled: true
    rules:
      min_24h_volume_usd: 50000000      # < $50M 排除
      min_orderbook_1pct_depth: 200000  # 1% 深度 < $200k 排除
      min_funding_periods_active: 3      # 资金费率连续3期非零
      min_listed_days: 90                # 上市 < 90 天排除

execution:
  max_slippage_pct: 0.3
  order_timeout_seconds: 5
  retry_on_failure: 2
  partial_fill_threshold_pct: 95.0
```

### 5.2 策略实例 `config/strategies/funding_rate_main.yaml`

```yaml
strategy_type: funding_rate_arb
instance_name: funding_rate_main
enabled: true

capital:
  allocated: 2000
  reserved_margin_pct: 20.0

scanning:
  exchanges: [binance, bybit, okx, htx, bitget, hyperliquid]
  scan_interval_seconds: 30
  symbols: ALL
  exclude_symbols: []
  min_24h_volume_usd: 10_000_000

entry_rules:
  min_apr: 15.0
  min_funding_rate: 0.005
  max_position_count: 5
  max_per_position: 500
  max_per_symbol: 500

exit_rules:
  exit_when_apr_below: 5.0
  exit_when_funding_negative_count: 2
  max_holding_hours: 168

risk:
  max_drawdown_per_position_pct: 5.0
  delta_neutral_tolerance_pct: 1.0

notifications:
  on_opportunity: [telegram, toast]
  on_entry: [telegram, toast]
  on_exit: [telegram, toast]
  on_funding_settled: [toast]
  on_warning: [telegram, discord, toast, system]
```

**新增一个策略实例 = 新增一个 yaml 文件 + 重启服务**,完全无需改代码。

---

## 六、关键技术选型

| 组件 | 选型 | 理由 |
|---|---|---|
| 后端语言 | Python 3.11+ | CCXT 支持最好,期权计算库丰富 |
| Web 框架 | FastAPI | 异步原生,WebSocket 支持好 |
| 异步 | asyncio + uvloop | uvloop 比默认快 2-4 倍 |
| 交易所 SDK | CCXT(CEX)+ 各自官方 SDK(DEX) | CCXT 封装 100+ CEX |
| 链上交互 | web3.py | EVM 链行业标准 |
| 数据库 | PostgreSQL + TimescaleDB | 事务型 + 时序统一 |
| 缓存 | Redis 7+ | Pub/Sub + 限流 + 锁 |
| 前端 | Next.js 14 + TypeScript | App Router 成熟 |
| UI 库 | shadcn/ui + Tailwind | 高度可定制 |
| 图表 | Recharts + TradingView Lightweight | TradingView 行业标杆 |
| 状态管理 | Zustand | 比 Redux 简单 10 倍 |
| 部署 | Docker Compose → Kubernetes | 起步用 Compose |

---

## 七、开发与部署路线图

### 7.1 开发阶段(8-12 周)

| 周次 | 任务 |
|---|---|
| W1 | 核心框架(event_bus, position_mgr, risk_engine 骨架) |
| W2 | CEX 交易所适配层(5 家) |
| W3 | DEX 交易所适配层(4 家) |
| W4 | 策略 1:资金费率套利 + paper trading |
| W5 | 策略 2:跨所基差套利 + paper trading |
| W6 | 策略 3:期现套利 + paper trading |
| W7 | 策略 4:CEX-DEX 监控 + paper trading |
| W8 | 策略 5:期权波动率(简化版) |
| W9 | 回测框架 + 历史数据回填 |
| W10 | 前端工程化(HTML → React) |
| W11 | 通知系统 + Docker 部署 |
| W12 | 集成测试 + 文档 + 应急手册 |

### 7.2 上线阶段(+4 周以上)

```
Week 13-14:Testnet 阶段
  - 交易所 testnet 跑通所有策略
  - 触发各种异常场景验证熔断

Week 15-16:Paper Trading
  - 实盘行情,模拟订单
  - 验证策略信号

Week 17+:小资金实盘
  - 第 1 周:$500
  - 第 2 周:$1000(若稳定)
  - 第 4 周:$2000
  - 第 8 周:$5000(完成 Phase 0)
```

**任何一阶段失败都要回退,不是硬上**。这是死规矩。

---

## 八、本份文档的剩余章节

### 核心章节(2-12)

后续核心章节按文件分别交付:

- **02_strategy_funding_rate.md** — 资金费率套利完整设计
- **03_strategy_basis_arb.md** — 跨所基差套利完整设计
- **04_strategy_spot_perp.md** — 期现套利完整设计
- **05_strategy_cex_dex.md** — CEX-DEX 套利完整设计
- **06_strategy_options_vol.md** — 期权波动率(含小资金限制说明)
- **07_exchange_adapters.md** — 9 家交易所接入规范
- **08_database_schema.md** — 数据库设计
- **09_notification_matrix.md** — 通知系统详细
- **10_backtest_framework.md** — 回测框架
- **11_deployment_and_ops.md** — 部署 + 应急手册
- **12_multi_user_authorization.md** — 多用户授权层(白名单模式)

### 扩展策略章节(13-17)

为 Phase 1 + Phase 2 阶段提供更多策略选择:

- **13_strategy_triangular.md** — 三角套利(Phase 0 第二个上线)
- **14_strategy_stablecoin.md** — 稳定币套利(简短版)
- **15_strategy_cross_exchange_spread.md** — 跨所价差套利
- **16_strategy_pairs_trading.md** — 永续合约对冲套利(Phase 1 重点)
- **17_strategy_dex_lp_hedged.md** — DEX LP + 对冲(Phase 1+ 才考虑)

---

## 九、未来策略候选清单

### 9.1 已经写文档但分阶段实施

| 策略 | 文档 | Phase 0 | Phase 1 ($30k+) | Phase 2 ($50k+) |
|---|---|---|---|---|
| 资金费率套利 | 第 2 章 | ✅ 主力 | ✅ 主力 | ✅ |
| 跨所基差套利 | 第 3 章 | ⚠️ 可选 | ✅ | ✅ |
| 期现套利 | 第 4 章 | ✅ 辅助 | ✅ | ✅ |
| CEX-DEX 监控 | 第 5 章 | ✅ 监控 | ✅ 监控 | ⚠️ 可考虑自动化 |
| 期权波动率 | 第 6 章 | ❌ 禁用 | ⚠️ 启用监控 | ✅ 实盘 |
| 三角套利 | 第 13 章 | ✅ 第 2 个上线 | ✅ | ✅ |
| 稳定币套利 | 第 14 章 | ❌ 收益太低 | ⚠️ 可加 | ✅ |
| 跨所价差套利 | 第 15 章 | ❌ | ✅ | ✅ |
| 永续对冲(配对) | 第 16 章 | ❌ | ✅ Phase 1 重点 | ✅ |
| DEX LP + 对冲 | 第 17 章 | ❌ | ⚠️ 评估 | ✅ |

### 9.2 永久排除的策略(写在这里防止你以后冲动想做)

#### ❌ MEV 套利(Searcher)

**为什么不做**:
- 行业被专业团队垄断(Top 10 团队占 85%+ MEV 市场)
- 散户成功率 < 5%,80% 亏损
- 需要 co-located 服务器、私有 mempool、Solidity/EVM 优化能力
- 资金门槛 $50k+(实际 $100k+ 才舒服)
- 6 个月技术学习曲线,期间无收入

**如果以后想做**:
- 等待资金 $200k+
- 招专业 Solidity 工程师 + EVM 优化专家
- 不要自己一个人做

#### ❌ 统计套利 / 因子模型

**为什么不做**:
- 资金门槛 $100k+(为了同时持有 20+ 仓位分散风险)
- 需要量化研究员(年薪 $150k-$500k)
- 因子有效性快速衰减,需要持续重训
- 个人 Quant 月化 -2% 到 +1%(80% 亏损)
- 跟现有策略本质重叠(资金费率/基差也是统计套利)

**如果以后想做**:
- 等资金 $500k+
- 招 PhD 量化研究员
- 不要自己一个人做

#### ❌ 高频做市(Market Making)

**为什么不做**(虽然你没问,但为防万一也说):
- 需要直连交易所(co-location),硬件成本 $5k+/月
- 需要专业的低延迟基础设施(C++/Rust)
- 跟职业做市商(Jump、Wintermute、Cumberland)直接竞争
- 散户做市做的是赔本买卖
- 跟"配对交易"完全不同——做市需要持续提供两边报价,风险敞口大

**如果以后想做**:
- 不要自己做
- 这不是个人能玩的游戏

### 9.3 不在路线图但偶尔会被问到的策略

#### NFT 套利
- **为什么不写**:NFT 市场流动性极差,套利机会稀少且难以自动化
- **建议**:如果你对 NFT 感兴趣,作为爱好玩,不作为系统策略

#### 跨链桥套利
- **为什么不写**:跨链桥本身有安全风险(2022-2024 年被盗超 $20 亿),不值得为了 0.1%-0.5% 套利冒整个本金风险
- **建议**:不做

#### 治理代币挖矿
- **为什么不写**:这不是套利,是"赚代币 → 卖代币"的方向性赌博
- **建议**:不在系统范围内

---

## 十、本份文档的剩余章节

### 你的下一步

1. **认真读完这一篇**(整体架构),特别是"项目现实预期声明"
2. **告诉我有没有疑问或不同意见**
3. **确认无误后,我会写第二篇**:资金费率套利的完整设计文档

**重要**:不要让我一次性把所有设计文档都写完。每写完一篇你都要 review,否则后面策略的设计会越偏越远,最后白做。

如果有任何一段你觉得"看不懂"或"看着不对",立刻指出来。**设计阶段的修改成本是 0,代码阶段是几倍,实盘阶段是钱。**
