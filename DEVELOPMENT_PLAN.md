# Dracula-System · 开发阶段路线图(8-12 周)

> **状态**:设计阶段已冻结,正式进入开发
> **总周期**:8-12 周(乐观 8 / 合理 10 / 保守 12)
> **里程碑**:Phase 0 实盘启动($500 → $5,000)
> **更新于**:2026-05-06

---

## 总览

```
┌─────────────────────────────────────────────────────────┐
│ Week 1-2:   项目骨架(地基)                            │
│ Week 3-4:   Binance 适配器 + 资金费率扫描器              │
│ Week 5-6:   风控引擎 + Position Manager                 │
│ Week 7-8:   回测框架 + 第一个策略验证                    │
│ Week 9-10:  Paper Trading 14 天                         │
│ Week 11-12: 实盘 Stage 1($500)                         │
└─────────────────────────────────────────────────────────┘
```

---

## Week 1-2:项目骨架

### 目标

搭出"地基",**不写任何业务逻辑**。

### 任务清单

#### 后端骨架

```
□ 1.1 创建目录结构
   backend/
   ├── app/
   │   ├── core/         # 配置加载、日志、依赖
   │   ├── exchanges/    # 交易所适配器(空,Week 3 填)
   │   ├── strategies/   # 策略(空,Week 5 填)
   │   ├── risk/         # 风控(空,Week 5 填)
   │   ├── execution/    # 订单执行(空,Week 5 填)
   │   ├── notifications/# 通知(空,Week 9 填)
   │   ├── api/          # FastAPI 路由
   │   └── models/       # Pydantic + SQLAlchemy
   ├── tests/
   ├── scripts/
   ├── pyproject.toml
   └── README.md

□ 1.2 pyproject.toml(用 uv 或 pip)
   依赖:
     - fastapi / uvicorn
     - sqlalchemy + asyncpg / psycopg2
     - redis-py
     - pydantic v2
     - pytest / pytest-asyncio
     - python-dotenv
     - structlog(结构化日志)
     - tenacity(重试)
     - ccxt(交易所统一接口)

□ 1.3 配置加载系统
   - 读 .env(用 pydantic-settings)
   - 读 config/*.yaml
   - Tier 1 / 2 / 3 分层(详见第 1 章 4.x 节)

□ 1.4 日志系统(structlog)
   - JSON 格式输出
   - 关键字段:strategy / exchange / symbol / event
   - 详见第 11 章
```

#### 数据库

```
□ 2.1 docker-compose.yml
   服务:
     - postgres:14-alpine
     - timescaledb:latest-pg14(从 timescale/timescaledb)
     - redis:7-alpine
   持久化卷
   健康检查
   network 配置

□ 2.2 数据库初始化脚本
   - schema 按第 8 章设计
   - 主要表:
     * users / api_keys
     * strategies / strategy_instances
     * positions / orders
     * risk_events / audit_log
     * pnl_daily(TimescaleDB hypertable)
     * funding_history(TimescaleDB hypertable)
     * orderbook_snapshot(TimescaleDB,可选)

□ 2.3 Alembic migration 设置
   - alembic init
   - 第一个 migration:创建所有表

□ 2.4 数据库连接池
   - 异步 SQLAlchemy
   - 连接配置从 .env 读
```

#### 配置文件

```
□ 3.1 config/config.yaml(主配置)
   按第 1 章 §5.1 模板,加入:
   - 三层熔断参数(3a / 3b / 3c)
   - 分层杠杆参数(Tier A / B / C)
   - Tier C 黑名单规则

□ 3.2 config/strategies/(每个策略一个 yaml)
   - funding_rate_main.yaml(Phase 0 主力)
   - spot_perp_main.yaml
   - triangular_main.yaml
   其他策略保留 enabled: false

□ 3.3 .env.example
   公开模板,真实值不提交
```

#### 工具脚本

```
□ 4.1 scripts/db_init.sh        # 数据库初始化
□ 4.2 scripts/health_check.py   # 系统健康检查
□ 4.3 scripts/start_dev.sh      # 本地开发启动
□ 4.4 scripts/stop_dev.sh       # 停止本地开发
```

#### 验证 checkpoints(必须 3 处通过)

```
□ Checkpoint 1: docker-compose up 后所有服务健康
□ Checkpoint 2: alembic upgrade head 后所有表创建
□ Checkpoint 3: scripts/health_check.py 全部通过
```

### 交付物

- 可运行的 docker-compose 环境
- 完整数据库 schema
- 配置加载 + 日志系统
- 但**不能交易**(尚无适配器和策略)

---

## Week 3-4:Binance 适配器 + 资金费率扫描器

### 目标

打通从 Binance 拉数据到入库的完整链路。

### 任务清单

```
□ 5.1 ExchangeBase 抽象类(详见第 7 章)
   接口:
   - async def get_balance(currency)
   - async def get_funding_rate(symbol)
   - async def place_order(...)
   - async def get_position(symbol)
   - async def stream_orderbook(symbol)
   - async def stream_ticker(symbol)

□ 5.2 BinanceAdapter 实现
   - REST: 用 ccxt
   - WebSocket: 用 binance-python 或 raw aiohttp
   - 重连机制:指数退避
   - 限流:per-second + per-minute

□ 5.3 funding_scanner.py
   每 5 分钟扫描所有合约的资金费率
   入库 funding_history 表
   推送 Redis pub/sub 给监控

□ 5.4 测试 mode
   .env 加 EXCHANGE_TESTNET=true
   连接 binance testnet,不影响实盘

□ 5.5 数据存储验证
   24 小时收集,验证数据连续性
```

### 验证 checkpoints

```
□ Checkpoint 1: BinanceAdapter 单元测试(mock)通过
□ Checkpoint 2: 集成测试(真 testnet)能下单
□ Checkpoint 3: funding_scanner 24h 无遗漏
```

---

## Week 5-6:风控引擎 + Position Manager

### 目标

实现**三层熔断 + 分层杠杆**,系统具备"自我保护"能力。

### 任务清单

```
□ 6.1 RiskEngine 核心
   - tick(): 每秒计算所有风控指标
   - check_tier3a(strategy_id, pnl): 单策略熔断
   - check_tier3b(account_pnl): 账户熔断
   - check_tier3c(account_pnl, margin_ratio): 强平
   - 动作分发(stop_new_orders / force_close / resume)

□ 6.2 LeverageController
   - 读取 config 分层杠杆
   - 接受策略 leverage 请求时验证 Tier
   - Tier C 黑名单实时检查(成交量 / 流动性)

□ 6.3 PositionManager
   - 实时 sync 持仓状态(交易所 vs DB)
   - 不一致时报警
   - 计算 portfolio Greeks(Phase 1 用)

□ 6.4 OrderExecutor
   - 统一下单接口
   - 重试机制
   - 滑点保护
   - 失败时通知
```

### 验证 checkpoints

```
□ Checkpoint 1: 单元测试覆盖所有风控分支
□ Checkpoint 2: 集成测试模拟 -3% / -5% 触发场景
□ Checkpoint 3: 故障注入测试(强制 API 错误,验证 Tier 3c)
```

---

## Week 7-8:回测框架 + 第一个策略验证

### 目标

让 #1 资金费率策略**通过回测门槛**:Sharpe > 1.5 / DD < 8% / Win Rate > 70%。

### 任务清单

```
□ 7.1 BacktestEngine(按第 10 章)
   - 历史数据 → tick replay
   - 模拟成交(考虑滑点 / 手续费)
   - 输出指标:Sharpe / Sortino / Calmar / Max DD / Win Rate / Profit Factor

□ 7.2 历史数据准备
   - Binance 12-24 个月 funding rate 数据
   - 现货 / 永续 OHLCV
   - 入库 / 列存

□ 7.3 策略 #1 资金费率套利实现
   按 docs/02_strategy_funding_rate.md
   关键参数:
   - 最低 APR 阈值
   - 最大持仓时间
   - 资金费率反转处理

□ 7.4 回测 + 调优
   - 跑 12 个月历史
   - 调阈值
   - 必须达标才能进 paper trading
```

### 验证 checkpoints

```
□ Checkpoint 1: 回测引擎对照真实成交,误差 < 5%
□ Checkpoint 2: 策略 #1 in-sample Sharpe > 1.5
□ Checkpoint 3: out-of-sample 验证不过拟合
```

---

## Week 9-10:Paper Trading 14 天

### 目标

用**实时行情 + 模拟订单**跑 14 天,暴露工程问题。

### 任务清单

```
□ 8.1 Paper Trading 模式开关
   - .env 加 TRADING_MODE=paper
   - 订单接口分流:真单 vs 模拟单
   - 模拟单按盘口模拟成交 + 加滑点

□ 8.2 通知系统(按第 9 章)
   - Telegram bot
   - Discord webhook
   - Email
   - PWA push
   - 系统级 desktop
   - 5 渠道 × 4 级别矩阵

□ 8.3 监控仪表盘
   先用 prototypes/dashboard_v1.0.html 接真数据
   或者 Grafana(更快)

□ 8.4 14 天观察 + Bug 修复
   每天看监控
   发现 bug → 修 → 重启计时
   连续 14 天无重大 bug 才能进实盘
```

### 验证 checkpoints

```
□ Checkpoint 1: Paper trading 7 天 mid-review,无 critical bug
□ Checkpoint 2: 14 天结束,paper 成绩 ≈ 回测预期(±20%)
□ Checkpoint 3: 故障演练(主动断 WS、kill DB)系统能恢复
```

---

## Week 11-12:实盘 Stage 1($500)

### 目标

**Stage 1 实盘验证**——不是赚钱,是验证"系统能正常工作不亏惨"。

### 任务清单

```
□ 9.1 Production deployment
   - 选择服务器(AWS / DigitalOcean Frankfurt)
   - SSL / 防火墙 / SSH key
   - 数据库备份策略
   - 日志中心化

□ 9.2 真实 API key 配置
   - 三层加密存储
   - 关闭 withdraw 权限
   - IP 白名单
   - 2FA 必开

□ 9.3 资金注入 $500
   - 划转到主账户
   - 系统自动配资到策略

□ 9.4 14 天观察
   - 实盘 vs Paper 误差监控
   - 触发风控的次数 / 类型
   - 心理建设:**触发风控不是失败**

□ 9.5 决定进 Stage 2 或修复
   - 如果 14 天稳定 → Stage 2 ($1,000)
   - 如果有 bug → 修 → 重新观察
   - 如果策略不对 → 回到 Week 7-8
```

### 验证 checkpoints

```
□ Checkpoint 1: 实盘 7 天 mid-review,确认无资金安全问题
□ Checkpoint 2: 14 天结束,实盘 Sharpe > 1.0(回测的 67%)
□ Checkpoint 3: 风控触发记录全部可解释
```

---

## 进度追踪规则

每周日,把本周进度更新到这份文档:

```
Week N · 2026-XX-XX

✅ 完成:
- [...]

⏳ 进行中:
- [...]

❌ 阻塞:
- [...]

下周计划:
- [...]
```

---

## 关键里程碑(可视化)

```
Week 0  ●─── 设计阶段冻结 ✅
Week 2  ●─── 项目骨架完成
Week 4  ●─── Binance 数据通畅
Week 6  ●─── 风控引擎可用
Week 8  ●─── 第一个策略回测通过
Week 10 ●─── Paper Trading 通过
Week 12 ●─── 实盘 $500 启动
        ↓
        Phase 1 评估
```

---

## 进度延迟时怎么办

按用户老虎接受的"8-12 周"时间表:

| 进度状态 | 行动 |
|---|---|
| 提前 1 周 | 不要急,质量优先 |
| 准时 | 继续 |
| 延迟 < 1 周 | 加紧,但不跳过 checkpoint |
| 延迟 > 1 周 | mid-review,看是否要砍功能 |
| 延迟 > 2 周 | **暂停一切**,重新评估范围 |

**绝对不能做**:
- ❌ 跳过 paper trading
- ❌ 跳过资金阶梯
- ❌ 跳过回测验证

---

## 技术债 / 后续优化

记录暂时妥协的地方,Phase 1 之后处理:

```
(初始为空,开发过程中填)
```
