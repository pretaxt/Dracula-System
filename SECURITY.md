# 安全规范

## 5 条铁律

### 1. 永远不 commit 凭证

`.gitignore` 已经排除了 `.env`、`*.key`、`wallet*.json` 等所有凭证文件。但每次 commit 前请用 `git diff` 检查一遍。

如果不小心 commit 了 API key:
1. **立即去交易所撤销该 key**(假设它已经泄露)
2. 用 BFG Repo-Cleaner 清除 git 历史
3. force push 覆盖远程

### 2. API key 最小权限

申请交易所 API key 时只勾选:
- ✅ 读取(Read)
- ✅ 现货交易
- ✅ 合约交易
- ❌ **绝对不勾"提币(Withdraw)"** — 即使 key 泄露,黑客也转不走资金
- ❌ API 转账

额外保护:
- 设置 IP 白名单(绑到你的 VPS)
- 每 3 个月轮换 key

### 3. DEX 用"交易钱包"模式

```
主钱包(硬件钱包,如 Ledger)─ 长期持有大额资产
       ↓ 定期手动转账
交易钱包(MetaMask) ─ 只放当前要交易的小额资金($1k-2k)
       │
       └─ 私钥放在 .env(永不 commit)
```

即使交易钱包私钥泄露,损失上限就是钱包内的钱。

### 4. AI 协助开发

**GitHub Copilot**(2026 年 4 月起)默认会用 Free/Pro/Pro+ 用户的互动数据训练模型,**包括从 Private 仓库读到的代码**。

务必:
- 关闭训练数据收集:[Settings → Copilot → Privacy](https://github.com/settings/copilot/features) 关闭 "Allow GitHub to use my data for AI model training"
- 或者改用 **Cursor + Claude API** / **Continue.dev**(明确不训练)

### 5. 实盘三阶段

绝对禁止跳过任何一阶段直接上实盘:

```
Stage 1:Testnet 验证(2 周)
  └─ 交易所 testnet 跑通所有策略,触发各种异常

Stage 2:Paper Trading(2 周)
  └─ 实盘行情,模拟订单,验证策略信号

Stage 3:小资金实盘(逐步加)
  └─ $500 → $1k → $2k → $5k 阶梯放大
```

任何一阶段失败都要回退,不是硬上。

## 实盘上线 checklist

- [ ] `.env` 已配置真实 key,**未 commit**
- [ ] 所有交易所 API key 关闭"提币"权限
- [ ] 所有交易所 API key 设置了 IP 白名单
- [ ] DEX 交易钱包资金限制在风险承受范围内
- [ ] 主资产在硬件钱包,与系统隔离
- [ ] Telegram Bot 绑定到你个人账号
- [ ] 紧急平仓脚本已测试
- [ ] 风控参数(`config.yaml`)已 review
- [ ] 系统熔断逻辑在 testnet 验证过
- [ ] 监控告警已配置
