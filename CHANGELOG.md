# Changelog

所有对 Dracula-System 设计文档和代码的重要变更都会记录在这里。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [v1.1] - 2026-05-06

### 设计阶段 Review 后冻结版本

#### 重大变更(用户老虎 review 后冻结)

- **风控熔断机制**:从"单层(触发即全平)"改为"**三层熔断**":
  - Tier 3a 单策略熔断(-3% 浮亏 → 停建仓 / 浮亏 -1% 自动恢复)
  - Tier 3b 账户熔断(账户单日 -3% / 周 -8% → 停建仓 / 必须人工介入)
  - Tier 3c 强制平仓(账户 -5% / 保证金率 < 50% → 强平 / 必须人工介入)

- **杠杆策略**:从"统一 3x"改为"**分层杠杆**":
  - Tier A 币种(BTC / ETH / SOL):最高 5x,-18% 强平
  - Tier B 币种(BNB / XRP / DOGE 等):最高 3x,-25% 强平
  - Tier C 币种(流动性低):**永久禁止**(成交量 < $50M / 上市 < 90 天 / 资金费率不连续)

- **多用户系统**:从"Phase 2 一次性做"改为"**拆两步**":
  - Phase 1 双用户家人版(第 16-20 周,2-3 人,简化版)
  - Phase 2 多用户朋友版(第 36-40 周,5-10 人,完整版)

#### 策略选择(从 17 个候选 → 12 个保留 + 5 个永久排除)

**保留 12 个**:#1 资金费率 / #2 跨所基差 / #4 期现 / #5 CEX-DEX(监控) / #6 期权 / #7 网格 / #9 做市 / #10 趋势 / #12 因子 / #13 三角 / #14 稳定币利率 / #16 配对

**永久排除 5 个**:#3 跨所价差 / #8 IDO / #11 ETF / #15 跨链桥 / #17 MEV

**注**:保留 ≠ 立即上实盘。所有 P1+ 策略需经过回测(Sharpe > 1.5)+ Paper Trading(14 天)+ 资金阶梯($500→$5k)三道关。

#### 文档变更

- 新增 `REVIEW_RESULT.md`(12 项决策最终结果 + 12 策略选择记录)
- 新增 `REVIEW_CHECKLIST_12_DECISIONS.md`(12 项决策详细说明)
- 新增 `REVIEW_CHECKLIST_12_DECISIONS.html`(交互式 review 工具)
- 新增 `STRATEGIES_17_COMPARISON.md`(17 策略详细对比)
- 新增 `STRATEGIES_17_SELECTOR.html`(交互式策略选择工具)
- 新增 `CHANGELOG.md`(本文件)

- 更新 `01_overview_and_architecture.md`:
  - 顶部加 Review v1.1 更新摘要
  - 4.3 熔断机制章节重写(三层熔断)
  - Tier 3 监控变量更新(分 3a/3b/3c)
  - yaml 配置示例更新(三层熔断参数 + 分层杠杆配置)

- 更新 `12_multi_user_authorization.md`:
  - 顶部加 Review v1.1 更新(双用户/多用户拆两步)

#### UI 原型

- `dashboard_v1.0.html` v1.0 完成
  - 6 个视图(总览 / 策略中心 / 持仓 / 风控 / 用户管理 / 设置)
  - 中英文双语切换(185 条翻译)
  - 日夜主题切换
  - 使用真实 logo(dgl_icon_square.PNG)
  - 主要文字黑色 / 标签灰色(对比度 WCAG AA 全过)

- `brand_identity.html` 品牌指南完成

---

## [v1.0] - 2026-05-05

### 设计阶段初版(待 review)

- 17 章设计文档完成(总计 273 页)
  - 第 1 章:总览与架构
  - 第 2-6 章:5 个核心策略详解
  - 第 7 章:交易所适配器
  - 第 8 章:数据库 schema
  - 第 9 章:通知矩阵
  - 第 10 章:回测框架
  - 第 11 章:部署与运维
  - 第 12 章:多用户授权层
  - 第 13-17 章:5 个补充策略详解

- 项目骨架(GitHub repo: pretaxt/Dracula-System)
  - README.md / LICENSE / SECURITY.md
  - .env.example / .gitignore

---

**版本说明**:
- v0.x:开发阶段(未发布)
- v1.x:设计阶段
- v2.x:Phase 0 实盘启动后
- v3.x:Phase 1+
