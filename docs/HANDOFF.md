# 🤝 Dracula-System 项目交接文档

> **给 Claude Code(或任何新协作者)的入口文档**
>
> 5 分钟读完这一份,你就能上手开发。

---

## 一、3 句话项目概况

1. **是什么**:多策略加密量化套利系统(Delta 中性为主),由用户老虎(Tiger)全职开发
2. **当前状态**:✅ 设计阶段冻结(17 章 + Review v1.1)/ ⏳ 开发阶段第 1 步(项目骨架)
3. **目标**:8-12 周做出 Phase 0 实盘版本($5,000 资金)

---

## 二、必读决策(冻结状态,**不要轻易改**)

### 风控红线(写死代码)

```yaml
# 三层熔断
Tier 3a 单策略熔断: 浮亏 -3% → 停建仓 / 浮亏 -1% 内自动恢复
Tier 3b 账户熔断: 单日 -3% / 周 -8% → 停建仓 / 必须 yaml 重启
Tier 3c 强制平仓: 单日 -5% / 保证金 < 50% → 强平 / 必须 yaml 重启

# 分层杠杆
Tier A 币种(BTC/ETH/SOL): 最高 5x, -18% 强平
Tier B 币种(BNB/XRP 等): 最高 3x, -25% 强平
Tier C 币种(流动性低): 永久禁止
```

**⚠️ 关键**:这些是 review 后 sign-off 的硬规则。Claude Code 不能自作主张改。

### 12 个保留策略(**P0 阶段只跑 3 个**) — 2026-05-07 更新

```
Phase 0 必跑(立即开发):
  #1  资金费率套利     $2,000  Delta 中性收 funding rate
  #4  期现套利         $1,000  现货 vs 永续基差收敛
  #13 三角套利         $500    同所 3 笔交易吃价差

Phase 1+ 候选(资金到位再开发):
  #2 跨所基差 / #3 跨所价差 / #5 CEX-DEX(只监控) / #6 期权(P0 禁用)
  #7 网格 / #9 做市 / #10 趋势
  #14 稳定币利率 / #16 配对交易

砍掉(不再纳入系统,UI/代码不展示):
  #8 IDO / #11 ETF / #12 因子模型 / #15 跨链桥 / #17 MEV
```

> **变更日志(2026-05-07)**:#3 从"永久排除"重启用,#12 从候选砍掉,#17 维持砍掉。详见 docs/CHANGELOG.md v1.2。

### 资金阶梯(**绝对不能跳级**)

```
Stage 1: $500   持续 14 天   验证执行链路
Stage 2: $1,000 持续 14 天   验证 2 交易所
Stage 3: $2,000 持续 14 天   验证多策略并行
Stage 4: $5,000 持续 30 天   Phase 0 目标
```

每阶段必须先回测(Sharpe > 1.5)+ Paper Trading(14 天)。

### 多用户系统(拆两步)

```
Phase 1 双用户家人版(第 16-20 周): 你 + 1-2 个家人
Phase 2 多用户朋友版(第 36-40 周): 你 + 5-10 个朋友
两者都是: 白名单 + 完全免费
```

**⚠️ 法律红线**:绝对不收费(任何形式都不行,包括"维护费")。

---

## 三、技术栈(已定型)

```
Backend:  Python 3.11
Frontend: Next.js
Data:     PostgreSQL + TimescaleDB + Redis
Git:      GitHub Desktop(用户不熟悉命令行)
部署:     Docker / docker-compose
```

---

## 四、工作流程(**用户老虎定的硬规则**)

### 1. 一步一步,绝不批量

```
❌ 错误:Claude Code 一次写完 5 个模块
✅ 正确:写 1 个模块 → 跟用户确认 → 用户说"继续" → 写下一个
```

### 2. 设计先行,代码后行

```
❌ 错误:看到需求就开始写代码
✅ 正确:先用 markdown / 表格说明设计 → 用户认可 → 才写代码
```

### 3. 三检验证

```
任何 bug 修复 / 新功能,验证至少 3 个 checkpoint:
  - 文件结构 / 代码正确性
  - 功能输出符合预期
  - 边界情况测试
```

### 4. 根因分析

```
❌ 错误:bug 修了表面就完事
✅ 正确:深入找根因,确保不会再出现
```

### 5. 不强迫用户回答

```
❌ 错误:用 ask_user_input_v0 弹强制选项
✅ 正确:有问题用文字简短说,继续做事;用户想答就答
```

---

## 五、当前位置(**Claude Code 从这里开始**)

### 已完成

- ✅ 17 章设计文档(`docs/01-17_*.md`)
- ✅ 12 项关键决策 review(`docs/REVIEW_RESULT.md`)
- ✅ 12 个策略选定(详见 REVIEW_RESULT)
- ✅ UI 原型(`prototypes/dashboard_v1.0.html`)
- ✅ 品牌指南(`prototypes/brand_identity.html`)
- ✅ Logo(`prototypes/dgl.PNG`)
- ✅ 完整 PDF(`dracula_design_v1.1.pdf`,298 页)

### 下一步:开发阶段第 1 步 — 项目骨架

具体任务清单见 `docs/DEVELOPMENT_PLAN.md` 第 1 周部分,大致是:

```
Week 1-2: 项目骨架
  □ backend/ frontend/ config/ 目录结构
  □ docker-compose.yml(Postgres + TimescaleDB + Redis)
  □ requirements.txt / pyproject.toml
  □ .env.example + .env.development
  □ 数据库 schema(按第 8 章设计)
  □ 配置加载系统(读 yaml)
  □ 日志系统(按第 11 章)
  □ 健康检查脚本
```

**关键约束**:
- 这一阶段 **不写任何业务逻辑**(不写策略代码、不写订单代码)
- 只搭"地基"
- 按用户"一步一步"原则,**搭一个组件就停下来确认**

---

## 六、关键文档地图

### 核心设计文档(必读)

| 文件 | 内容 | 何时读 |
|---|---|---|
| `docs/01_overview_and_architecture.md` | 总览 + 架构 + 风控设计 | 开始时通读 |
| `docs/08_database_schema.md` | PostgreSQL + TimescaleDB schema | Week 1 必读 |
| `docs/11_deployment_and_ops.md` | Docker / 监控 / 日志 | Week 1 必读 |
| `docs/REVIEW_RESULT.md` | 12 项决策结果(冻结) | 开始时通读 |
| `docs/CHANGELOG.md` | 变更历史 | 参考用 |

### 策略实施文档(开发对应策略时读)

| 文件 | 策略 | 优先级 |
|---|---|---|
| `docs/02_strategy_funding_rate.md` | #1 资金费率套利 | P0 必做 |
| `docs/04_strategy_spot_perp.md` | #4 期现套利 | P0 必做 |
| `docs/13_strategy_triangular.md` | #13 三角套利 | P0 必做 |
| `docs/03_strategy_basis_arb.md` | #2 跨所基差套利 | P1 |
| `docs/14_strategy_stablecoin.md` | #14 稳定币利率 | P1 |
| `docs/16_strategy_pairs_trading.md` | #16 配对交易 | P1 |
| `docs/05_strategy_cex_dex.md` | #5 CEX-DEX(只监控) | P1 |
| `docs/06_strategy_options_vol.md` | #6 期权(P0 禁用) | P2 |

### 系统设计文档

| 文件 | 内容 |
|---|---|
| `docs/07_exchange_adapters.md` | 交易所适配器接口设计 |
| `docs/09_notification_matrix.md` | 通知矩阵(5 渠道 × 4 级别) |
| `docs/10_backtest_framework.md` | 回测框架 |
| `docs/12_multi_user_authorization.md` | 多用户授权(Phase 1 双 / Phase 2 多) |

### Review 文档(决策依据)

| 文件 | 内容 |
|---|---|
| `docs/REVIEW_CHECKLIST_12_DECISIONS.md` | 12 项决策详细说明 |
| `docs/STRATEGIES_17_COMPARISON.md` | 17 策略对比 |

---

## 七、用户介绍

**用户名**:老虎(Tiger)
**GitHub**:pretaxt
**资金**:$35,000(初始)
**状态**:全职做项目
**经验**:有交易经验,但不是专业 quant

**沟通风格**:
- 直接,不喜欢废话
- 喜欢"想清楚再做"
- 重视 root cause,不接受表面修复
- **会主动反对** Claude 的不合理建议(这是好事,不要让步)

---

## 八、安全 / 隐私 (重要!)

- ❌ **永远不要把 API key 提交到 Git**
- ❌ **永远不要在代码里写死交易所凭证**
- ✅ 所有 secrets 走 `.env`(已 gitignore)
- ✅ 生产环境用环境变量
- ✅ 多用户阶段用三层加密(详见第 12 章)

---

## 九、给 Claude Code 的具体起步指南

打开仓库后,**按这个顺序**:

```
Step 1: 读这份 HANDOFF.md(就是这份)
Step 2: 读 docs/01_overview_and_architecture.md(20 分钟)
Step 3: 读 docs/REVIEW_RESULT.md(2 分钟,理解决策)
Step 4: 读 docs/DEVELOPMENT_PLAN.md(本次开发的具体任务)
Step 5: 跟用户对一下"我准备从 X 开始,可以吗?"
Step 6: 用户确认后,开始写第一个文件
```

**不要做的事**:
- ❌ 不要一开始就读全部 17 章(没必要,需要时再读)
- ❌ 不要直接写代码(先确认设计)
- ❌ 不要批量修改文件(一次一个)

---

## 十、紧急联系 / 已知问题

### 当前已知问题
- 无(设计阶段刚完成)

### 需要 Claude Code 注意的边界
- 用户对**杠杆**和**风控**特别敏感,不要主动建议放宽
- 用户对**收费 / 商业化**有明确底线,不要建议
- 用户偏好**渐进式开发**,不喜欢"一次性大改"

---

## 文档版本

- v1.0 · 2026-05-06 · 设计阶段交接版

**当下次有重大变更时,更新这份文档 + CHANGELOG.md**。
