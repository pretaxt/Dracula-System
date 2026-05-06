# 🦇 Dracula-System

> **多策略加密货币量化套利系统** · 跨 5 家中心化交易所 + DEX

[![Status](https://img.shields.io/badge/status-design%20frozen%20v1.1-c41e3a)]()
[![Phase](https://img.shields.io/badge/phase-Phase%200%20開發中-orange)]()
[![License](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)

---

## ⚠️ 风险声明

本项目处于**开发阶段**,尚未完成 Phase 0 实盘验证。

这不是"自动赚钱"的工具,而是一个量化套利的**框架**——它提供执行能力,但不保证盈利。

使用本系统进行交易的人:**必须自行理解策略原理,必须接受可能的资金损失,必须对自己的交易决策负责**。作者不对任何使用本系统造成的损失承担责任(详见 [LICENSE](./LICENSE))。

---

## 当前状态

```
✅ 设计阶段冻结(2026-05-06)
   - 17 章设计文档(详见 docs/)
   - 12 项关键决策已 review
   - 12 个保留策略选定
   - UI 原型完成

🔄 开发阶段(进行中)
   - Week 1-2: 项目骨架(进行中)
   - Week 3-4: Binance 适配器
   - Week 5-6: 风控引擎
   - Week 7-8: 回测框架
   - Week 9-10: Paper Trading
   - Week 11-12: 实盘 $500 启动
```

---

## 🚀 快速上手

### 给项目主理人

```bash
git clone https://github.com/pretaxt/Dracula-System.git
cd Dracula-System

# 看完整设计 PDF
open dracula_design_v1.1.pdf

# 或读 markdown
cat docs/HANDOFF.md  # 5 分钟概览
```

### 给 AI 协作者(Claude Code)

```bash
# 必读文件,按顺序
1. .claude_code_init.md       # 工作约定
2. docs/HANDOFF.md             # 项目交接
3. docs/DEVELOPMENT_PLAN.md    # 开发路线图
4. docs/REVIEW_RESULT.md       # 决策记录(冻结)
```

---

## 📚 文档地图

### 入口文档

| 文档 | 用途 | 阅读时间 |
|---|---|---|
| [HANDOFF.md](docs/HANDOFF.md) | **项目交接,新人必读** | 5 分钟 |
| [DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md) | 8-12 周开发路线 | 10 分钟 |
| [REVIEW_RESULT.md](docs/REVIEW_RESULT.md) | 12 项决策结果(冻结) | 2 分钟 |
| [.claude_code_init.md](./.claude_code_init.md) | AI 协作约定 | 5 分钟 |
| [CHANGELOG.md](docs/CHANGELOG.md) | 变更日志 | 参考用 |

### 核心设计

| 章节 | 内容 |
|---|---|
| [01 总览与架构](docs/01_overview_and_architecture.md) | 风控 / 杠杆 / 配置(必读) |
| [08 数据库 Schema](docs/08_database_schema.md) | PostgreSQL + TimescaleDB |
| [11 部署与运维](docs/11_deployment_and_ops.md) | Docker / 监控 / 日志 |
| [12 多用户授权](docs/12_multi_user_authorization.md) | Phase 1 双用户 / Phase 2 多用户 |

### 策略文档

| 策略 | 文档 | 阶段 |
|---|---|---|
| #1 资金费率套利 | [02](docs/02_strategy_funding_rate.md) | P0 必做 |
| #4 期现套利 | [04](docs/04_strategy_spot_perp.md) | P0 必做 |
| #13 三角套利 | [13](docs/13_strategy_triangular.md) | P0 必做 |
| #2 跨所基差 | [03](docs/03_strategy_basis_arb.md) | P1 |
| #14 稳定币利率 | [14](docs/14_strategy_stablecoin.md) | P1 |
| #16 配对交易 | [16](docs/16_strategy_pairs_trading.md) | P1 |
| #5 CEX-DEX(监控) | [05](docs/05_strategy_cex_dex.md) | P1 |
| #6 期权波动率 | [06](docs/06_strategy_options_vol.md) | P2 |

### 系统组件

- [07 交易所适配器](docs/07_exchange_adapters.md)
- [09 通知矩阵](docs/09_notification_matrix.md)
- [10 回测框架](docs/10_backtest_framework.md)
- [17 DEX LP 对冲](docs/17_strategy_dex_lp_hedged.md)

### Review 文档

- [12 项决策详细说明](docs/REVIEW_CHECKLIST_12_DECISIONS.md)
- [17 策略对比](docs/STRATEGIES_17_COMPARISON.md)
- [REVIEW_CHECKLIST_12_DECISIONS.html](docs/REVIEW_CHECKLIST_12_DECISIONS.html)(交互式)
- [STRATEGIES_17_SELECTOR.html](docs/STRATEGIES_17_SELECTOR.html)(交互式)

---

## 🎨 UI 原型

```
prototypes/
├── dashboard_v1.0.html      # 6 视图主仪表盘(中英双语 + 日夜)
├── brand_identity.html      # 品牌指南
├── dgl.PNG                  # 主 logo(原始 1024x1024)
└── dgl_icon_square.PNG      # 方形 icon(512x512,UI 用)
```

---

## 💡 关键决策(冻结)

### 风控

```yaml
三层熔断:
  Tier 3a 单策略熔断: -3% 浮亏 → 停建仓 / -1% 内自动恢复
  Tier 3b 账户熔断:   单日 -3% / 周 -8% → 停建仓 / 必须 yaml 重启
  Tier 3c 强制平仓:   单日 -5% / 保证金 < 50% → 强平 / 必须 yaml 重启

分层杠杆:
  Tier A (BTC/ETH/SOL):  最高 5x, -18% 强平
  Tier B (BNB/XRP 等):   最高 3x, -25% 强平
  Tier C (流动性低):     永久禁止
```

### 资金阶梯

```
Stage 1: $500   持续 14 天
Stage 2: $1,000 持续 14 天
Stage 3: $2,000 持续 14 天
Stage 4: $5,000 持续 30 天 ← Phase 0 目标
```

### 多用户

```
Phase 1: 双用户家人版(第 16-20 周)
Phase 2: 多用户朋友版(第 36-40 周)
共同原则:白名单 + 完全免费
```

---

## 🛠️ 技术栈

```
Backend:    Python 3.11 + FastAPI + asyncio
Database:   PostgreSQL 14 + TimescaleDB + Redis 7
Frontend:   Next.js + Tailwind CSS
Containers: Docker + docker-compose
Logs:       structlog (JSON)
Tests:      pytest + pytest-asyncio
```

---

## 📋 项目工作流原则

1. **设计先行,代码后行** — 决策类工作必须先 markdown 说明
2. **一步一步** — 不批量,1 个完成 → 确认 → 下一个
3. **三检验证** — 任何 bug fix / feature 至少 3 个 checkpoint
4. **根因分析** — 找到根因再修,不接受表面修复

---

## 📞 项目主理人

- **GitHub**: [@pretaxt](https://github.com/pretaxt)
- **Status**: 全职开发中

---

## License

MIT License — 详见 [LICENSE](./LICENSE)
