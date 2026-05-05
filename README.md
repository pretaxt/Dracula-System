# Dracula-System

> 多策略加密货币量化套利系统 · 跨 9 家交易所(CEX + DEX)

---

## ⚠️ 声明

本项目处于**设计阶段**,尚未完成开发。

这不是一个能让你"自动赚钱"的工具,而是一个量化套利的**框架**——它提供执行能力,但不保证盈利。

使用本系统进行交易的人:必须自行理解策略原理,必须接受可能的资金损失,必须对自己的交易决策负责。**作者不对任何使用本系统造成的损失承担责任**(详见 LICENSE)。

## 目标

构建一个可插拔、事件驱动、风控前置的多策略量化套利系统:

- 5 大策略并行运行(资金费率套利 / 跨所基差 / 期现套利 / CEX-DEX 监控 / 期权波动率)
- 9 家交易所统一接入(Binance / Bybit / OKX / HTX / Bitget / Hyperliquid / dYdX / Uniswap / PancakeSwap)
- 三层风控分级(策略级 / 账户级 / 系统级)
- 多渠道通知(Telegram / Discord / Email / Toast)
- 实时仪表盘 + 历史回测 + PnL 归因

## 现实预期

| 策略 | 预期月化 | 最大单月回撤 |
|---|---|---|
| 资金费率套利 | 1.0% - 2.0% | -3% |
| 跨所基差套利 | 0.5% - 1.5% | -2% |
| 期现套利 | 0.8% - 1.5% | -2% |
| 期权波动率 | -10% 到 +20% | -30% |

**整体月化预期:1% - 3%**。年化 100%+ 那种东西不存在。

## 当前进度

- [x] 整体架构设计 — [docs/01](docs/01_overview_and_architecture.md)
- [x] UI 原型 — [prototypes/dashboard_v0.3.html](prototypes/dashboard_v0.3.html)
- [ ] 资金费率套利策略设计
- [ ] 跨所基差套利策略设计
- [ ] 期现套利策略设计
- [ ] CEX-DEX 套利策略设计
- [ ] 期权波动率策略设计
- [ ] 核心框架实现
- [ ] 交易所适配层
- [ ] 回测引擎
- [ ] 前端工程化
- [ ] 部署文档

预计开发周期:**8-12 周**

## 技术栈

- **后端**:Python 3.11+ / FastAPI / asyncio + uvloop
- **前端**:Next.js 14 / TypeScript / Tailwind / shadcn/ui
- **数据库**:PostgreSQL + TimescaleDB
- **缓存/事件**:Redis 7+
- **交易所接入**:CCXT(CEX)+ 各 DEX 官方 SDK
- **部署**:Docker Compose

## 安全规范

⚠️ **使用前务必阅读** [SECURITY.md](SECURITY.md)

核心规则:
1. 永远不要 commit `.env` 或任何凭证文件(`.gitignore` 已排除)
2. 交易所 API key **只勾"读 + 交易",不勾"提币"**
3. DEX 用专门的"交易钱包",大额资产留在硬件钱包
4. 实盘前必须经过 testnet → paper trading → 小资金 三阶段验证

## 目录结构

```
Dracula-System/
├── docs/              设计文档
├── prototypes/        UI 原型
├── core/              核心框架(策略基类、风控、执行、事件总线)
├── strategies/        策略实现(可插拔)
├── exchanges/         交易所适配层
├── api/               FastAPI 后端
├── frontend/          Next.js 前端
├── notifications/     通知系统
├── backtest/          回测引擎
├── data/              数据访问层
├── config/            配置文件
├── scripts/           运维脚本
└── tests/             测试
```

## License

MIT — 见 [LICENSE](LICENSE)
