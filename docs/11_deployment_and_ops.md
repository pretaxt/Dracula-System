# 11 · 部署与应急手册 · 完整设计

> **范围**:从开发环境到生产部署 + 出问题时怎么处理
> **优先级**:🟢 P0(没有这个,实盘是裸奔)
> **核心原则**:**为最坏情况做准备**——你迟早会遇到 API 宕机、保证金不足、被攻击

---

## 一、部署架构

### 1.1 三种部署模式

| 模式 | 资金规模 | 推荐配置 | 月成本 |
|---|---|---|---|
| **本地开发** | $0(测试) | MacBook + Docker Desktop | $0 |
| **小型 VPS** | $1k - $50k | 单台 2 核 4GB / 80GB SSD | $5-15 |
| **中型生产** | $50k - $500k | 单台 8 核 16GB / 200GB SSD + 备份 | $40-80 |
| **机构级** | $500k+ | K8s 集群 + 跨区域 | $300+ |

**Phase 0 推荐:小型 VPS**

理由:
- 实盘必须 7×24 跑,不能依赖你的笔记本
- 离交易所机房近的 VPS 延迟低(亚洲选东京/新加坡,欧美选弗吉尼亚/法兰克福)
- 一键备份恢复,丢电脑不丢数据

### 1.2 推荐供应商

| 供应商 | 优势 | 注意 |
|---|---|---|
| **Vultr** | 按小时计费,全球节点 | $6/月起步 |
| **DigitalOcean** | 文档全,新手友好 | $6/月起步 |
| **Hetzner** | 最便宜,德国数据中心 | $4/月起步,但欧洲延迟 |
| **AWS Lightsail** | AWS 入门版 | $5/月起步 |
| **阿里云海外** | 中国大陆访问快 | 价格略贵 |

**避免**:中国大陆 VPS——加密货币业务不合规,风险大。

### 1.3 网络位置选择

策略对延迟敏感,选最近的 VPS:

| 主交易所 | 推荐 VPS 区域 | 预期延迟 |
|---|---|---|
| Binance(主服务器东京) | 东京 / AWS Tokyo | 5-15ms |
| Bybit(新加坡) | 新加坡 | 5-10ms |
| OKX(香港) | 香港 / 新加坡 | 10-20ms |
| Hyperliquid(全球 CDN) | 任何地方 | 50-100ms |

**Phase 0 建议**:东京或新加坡,兼顾 Binance / Bybit / OKX。

---

## 二、技术栈

### 2.1 Docker Compose

整个系统用 Docker Compose 一键启动:

```yaml
# docker-compose.yml
version: '3.9'

services:
  postgres:
    image: timescale/timescaledb:latest-pg16
    restart: unless-stopped
    environment:
      POSTGRES_DB: dracula
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init_db.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "127.0.0.1:5432:5432"  # 只允许本地访问
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - redis_data:/data
    ports:
      - "127.0.0.1:6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s

  api:
    build: .
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    environment:
      - POSTGRES_HOST=postgres
      - REDIS_URL=redis://redis:6379/0
    env_file:
      - .env
    ports:
      - "127.0.0.1:8000:8000"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s

  strategy_engine:
    build: .
    command: python -m core.main
    restart: unless-stopped
    depends_on:
      api:
        condition: service_healthy
    env_file:
      - .env
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs

  frontend:
    build: ./frontend
    restart: unless-stopped
    ports:
      - "127.0.0.1:3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:8000

volumes:
  postgres_data:
  redis_data:
```

### 2.2 端口暴露策略

**绝对不要**直接把端口暴露到公网。

- PostgreSQL / Redis:**只 bind 到 127.0.0.1**,只能本机访问
- API:**只 bind 到 127.0.0.1**,通过 Nginx 反代
- Frontend:同上
- 通过 Caddy / Nginx 加 HTTPS + Basic Auth + IP 白名单

```nginx
# /etc/nginx/sites-available/dracula
server {
    listen 443 ssl http2;
    server_name dracula.yourdomain.com;

    # SSL(用 Certbot 自动续签 Let's Encrypt 证书)
    ssl_certificate /etc/letsencrypt/live/dracula.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/dracula.yourdomain.com/privkey.pem;

    # IP 白名单(只允许你自己访问)
    allow YOUR_HOME_IP;
    allow YOUR_OFFICE_IP;
    deny all;

    # Basic Auth 双重保险
    auth_basic "Dracula System";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # 前端
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # API
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://127.0.0.1:8000/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

---

## 三、首次部署步骤

### 3.1 VPS 初始化

```bash
# 1. 创建非 root 用户
adduser dracula
usermod -aG sudo dracula
su - dracula

# 2. SSH 公钥登录(禁用密码)
mkdir -p ~/.ssh
echo "your_ssh_public_key" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# 3. 加固 SSH
sudo nano /etc/ssh/sshd_config
# PasswordAuthentication no
# PermitRootLogin no
# Port 22222(改个非默认端口)
sudo systemctl restart sshd

# 4. 防火墙
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22222/tcp   # SSH
sudo ufw allow 443/tcp     # HTTPS
sudo ufw enable

# 5. 自动安全更新
sudo apt install unattended-upgrades
sudo dpkg-reconfigure unattended-upgrades

# 6. fail2ban(防暴力破解)
sudo apt install fail2ban
sudo systemctl enable fail2ban
```

### 3.2 安装依赖

```bash
# Docker + Docker Compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker dracula
sudo systemctl enable docker

# 实用工具
sudo apt install -y git htop tmux ncdu jq
```

### 3.3 部署项目

```bash
# 1. 拉代码
cd ~
git clone https://github.com/pretaxt/Dracula-System.git
cd Dracula-System

# 2. 配置环境变量
cp .env.example .env
nano .env  # 填入真实 API key 和密码
chmod 600 .env  # 严格权限

# 3. 启动
docker compose up -d

# 4. 查看日志
docker compose logs -f
```

### 3.4 验证部署

```bash
# 检查所有容器健康
docker compose ps

# 应该看到所有都是 (healthy):
# postgres        Up X minutes (healthy)
# redis           Up X minutes (healthy)
# api             Up X minutes (healthy)
# strategy_engine Up X minutes
# frontend        Up X minutes

# 测试 API
curl http://127.0.0.1:8000/health

# 应该返回:
# {"status": "ok", "version": "0.1.0", "uptime_seconds": 123}
```

---

## 四、监控

### 4.1 日志管理

**集中收集**:

```yaml
# 在 docker-compose.yml 中
logging:
  driver: "json-file"
  options:
    max-size: "100m"
    max-file: "3"
```

**重要日志保留**:
- 系统启停事件 → PostgreSQL `system_events` 表
- 风控事件 → PostgreSQL `risk_events` 表
- 通知发送记录 → PostgreSQL `notifications` 表
- 调试日志 → Docker 日志(滚动)

### 4.2 健康检查

每个组件都要有 `/health` 端点:

```python
# api/health.py
@app.get("/health")
async def health():
    checks = {
        "database": await check_db(),
        "redis": await check_redis(),
        "exchanges": await check_exchanges(),
        "strategies": await check_strategies(),
    }

    healthy = all(c["ok"] for c in checks.values())
    return {
        "status": "ok" if healthy else "degraded",
        "checks": checks,
        "timestamp": datetime.utcnow().isoformat(),
    }
```

### 4.3 外部监控

用免费的 **UptimeRobot** 监控:
- 每 5 分钟探测 `/health`
- 失败 2 次连续 → 发邮件 + Telegram 告警
- 这是最后一道防线,即使你的系统挂了,你也能立刻知道

### 4.4 日常巡检清单

```
每天(每天早上 5 分钟):
☐ 仪表盘所有指标正常
☐ 没有未读 CRITICAL 通知
☐ 当日 PnL 在合理范围
☐ 所有交易所连接 OK
☐ 系统日志无异常错误

每周(周一 30 分钟):
☐ 检查上周 PnL 归因(费用/滑点是否合理)
☐ 检查所有持仓的状态
☐ 检查数据库占用空间
☐ 检查日志大小
☐ 测试一次紧急平仓脚本(在测试环境)

每月(月初 1-2 小时):
☐ 完整对账(交易所余额 vs 系统记录)
☐ 性能 review(Sharpe / MaxDD 是否符合预期)
☐ 安全扫描(GitGuardian 扫一遍仓库)
☐ 备份 restore 演练
☐ API key 是否需要轮换
☐ 评估是否扩容资金
```

---

## 五、备份策略

### 5.1 自动备份

```bash
# /home/dracula/scripts/backup.sh
#!/bin/bash
set -e

BACKUP_DIR=/home/dracula/backups
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR

# PostgreSQL 全量备份
docker compose exec -T postgres pg_dump -U $POSTGRES_USER $POSTGRES_DB | gzip > $BACKUP_DIR/db_$DATE.sql.gz

# 配置文件备份
tar czf $BACKUP_DIR/config_$DATE.tar.gz config/

# 上传到异地(用 rclone 或 aws s3)
rclone copy $BACKUP_DIR/db_$DATE.sql.gz remote:dracula-backups/db/
rclone copy $BACKUP_DIR/config_$DATE.tar.gz remote:dracula-backups/config/

# 清理本地超过 7 天的
find $BACKUP_DIR -mtime +7 -delete
```

```bash
# 加到 crontab
0 3 * * * /home/dracula/scripts/backup.sh >> /var/log/dracula_backup.log 2>&1
```

### 5.2 备份验证

每月做一次"恢复演练":

```bash
# 1. 启一个测试容器
docker run --name test-restore -e POSTGRES_PASSWORD=test -d timescale/timescaledb:latest-pg16

# 2. 恢复最近的备份
gunzip < backups/db_20260501.sql.gz | docker exec -i test-restore psql -U postgres

# 3. 验证关键表
docker exec test-restore psql -U postgres -c "SELECT COUNT(*) FROM positions;"

# 4. 销毁
docker rm -f test-restore
```

---

## 六、应急 Runbook(最重要的部分)

下面是真实场景的处理流程。**在出问题之前打印一份贴在墙上**。

### 6.1 系统熔断了

**触发**:你收到 CRITICAL 通知:"SYSTEM HALTED — 单日回撤触及 -3% 红线"

**处理流程**:

```
[1] 别慌
    系统已经自动平仓所有持仓,资金安全
    用 5 分钟先深呼吸,不要冲动操作

[2] 登录仪表盘 / SSH 到服务器
    https://dracula.yourdomain.com
    或 ssh -p 22222 dracula@your.vps.ip

[3] 调查根因
    查看 risk_events 表的最近事件:
    docker compose exec postgres psql -U $USER -d $DB -c \
        "SELECT * FROM risk_events ORDER BY created_at DESC LIMIT 10;"

    查看最近的持仓:
    SELECT * FROM positions
    WHERE closed_at > NOW() - INTERVAL '24 hours'
    ORDER BY closed_at DESC;

    查看交易所端的实际余额(对账)

[4] 判断原因
    A. 策略本身亏损 → 是不是市场极端行情?
    B. Bug 导致错误平仓 → 立即关闭策略,review 代码
    C. 交易所故障 → 等恢复后再启
    D. 误触发(实际没真亏)→ 调高阈值,但要审慎

[5] 决定恢复 or 维护
    恢复 = 重启服务,允许新仓
    维护 = 暂停几天观察,分析根因

[6] 如果决定恢复:
    docker compose restart strategy_engine

[7] 通知自己:在仪表盘标记"已确认熔断,系统恢复"
```

### 6.2 API key 泄露

**触发**:你不小心把 .env commit 到了 GitHub

**处理流程(每一步都要快,你只有几分钟)**:

```
[1] 立即去交易所撤销 API key
    Binance:Account → API Management → Delete
    其他交易所同理
    时间窗口:5 分钟内必须做完

[2] 创建新的 API key
    继续遵守"最小权限"(只读 + 交易,不要提币)
    设置 IP 白名单

[3] 更新 .env
    nano .env
    把所有 API key 替换为新的

[4] 清理 git 历史
    用 BFG Repo-Cleaner:
    bfg --delete-files .env
    bfg --replace-text passwords.txt
    git reflog expire --expire=now --all
    git gc --prune=now --aggressive
    git push --force

[5] 重启系统
    docker compose down
    docker compose up -d

[6] 检查交易所交易历史 24 小时
    确认没有异常交易
    如果有,立即提交工单给交易所
```

### 6.3 交易所突然宕机

**触发**:Binance API 持续 10 分钟返回 502

**处理流程**:

```
[1] 系统应该已经自动暂停下单(API 错误熔断)
    确认通知中有 "WARN: Binance API errors" 类似消息

[2] 查看交易所状态页
    https://status.binance.com/
    https://twitter.com/binance(社交媒体最快)

[3] 评估当前持仓的风险
    如果只是 API 慢,持仓还在,风险可控
    如果整个交易所长时间(>1 小时)瘫痪,考虑通过其他交易所对冲风险

[4] 通过备用通道平仓(罕见但要会)
    如果 API 完全瘫痪,但交易所网页还能登录:
    手动登录 → 平仓 → 在系统里手动标记 position 为 closed

[5] 等 API 恢复
    重新启动 strategy_engine:
    docker compose restart strategy_engine

[6] 复盘:这次事件的影响
    手续费、滑点、未实现损失
    更新到 risk_events 表
```

### 6.4 数据库挂了

**触发**:策略日志中大量 "database connection failed"

**处理流程**:

```
[1] 检查数据库容器
    docker compose ps postgres

[2] 如果是 unhealthy
    docker compose logs postgres --tail 100

[3] 常见原因
    A. 磁盘满 → df -h 查,清理日志或扩容
    B. 内存不足 → free -m 查
    C. 数据损坏 → 罕见,但需要从备份恢复

[4] 重启
    docker compose restart postgres
    等 30 秒,直到 healthy

[5] 重启依赖服务
    docker compose restart api strategy_engine

[6] 验证数据完整性
    SELECT COUNT(*) FROM positions WHERE status = 'open';
    确认数量与交易所端一致
```

### 6.5 浮亏严重(-2% 但没触发熔断)

**触发**:仪表盘显示当日浮亏 -2%,接近 -3% 红线

**处理流程**:

```
[1] 不要恐慌平仓
    Delta 中性策略的浮亏是正常的(基差扩大)
    强行平仓可能错失收敛

[2] 评估浮亏来源
    SELECT
      strategy_type,
      symbol,
      unrealized_pnl,
      entry_basis_pct,
      current_basis_pct
    FROM positions
    WHERE status = 'open'
    ORDER BY unrealized_pnl ASC;

[3] 检查保证金率
    如果保证金率仍 > 60%,继续观察
    如果 < 60%,系统会自动减半仓
    如果 < 50%,强制平仓

[4] 主动减仓的判断
    如果浮亏来自资金费率反转(基本面坏了)→ 主动平仓
    如果浮亏来自基差扩大(技术性)→ 继续持有

[5] 在仪表盘里手动减仓
    点击对应仓位的"平 50%"或"全平"按钮
    确认两次(防误触)
```

### 6.6 钱包私钥泄露(DEX 部分)

**触发**:你怀疑钱包私钥泄露(电脑被入侵、私钥不小心截图等)

**处理流程**:

```
[1] 立即创建新钱包
    用硬件钱包(Ledger / Trezor)生成
    或用全新设备生成

[2] 把所有资金从旧钱包转出
    速度比攻击者快是关键
    建议先转 1 美元试探,看到账后再大额转
    用 Flashbots Protect 防 MEV 抢跑

[3] 撤销旧钱包的所有授权(approve)
    用 Revoke.cash https://revoke.cash/
    撤销所有 ERC20 / ERC721 授权
    重要:不撤销的话,即使资金转走,合约还能调用 approve 的地址

[4] 更新 .env
    把 TRADING_WALLET_PRIVATE_KEY 改成新的

[5] 重启系统
    docker compose down
    docker compose up -d

[6] 监控旧钱包链上活动
    用 Etherscan 监控:
    https://etherscan.io/address/old_wallet
    任何异常交易立即记录
```

---

## 七、紧急停机脚本

```python
# scripts/emergency_close_all.py
"""紧急平仓所有持仓的脚本

使用场景:
  - 系统熔断后人工确认无法继续
  - 即将参加测试/检查,要先清空仓位
  - 发现重大 bug,先停后查

用法:
  python scripts/emergency_close_all.py --confirm

注意:
  - 只能由你本人手动运行
  - 会用 MARKET 单平仓(滑点大但确定性高)
  - 平完后系统进入 HALTED 状态,需要手动重启
"""

import asyncio
from core.position_manager import PositionManager
from core.execution_engine import ExecutionEngine

async def main():
    pm = PositionManager()
    ee = ExecutionEngine()

    open_positions = await pm.get_all_open()
    print(f"Found {len(open_positions)} open positions")

    confirm = input("Type 'CLOSE ALL' to confirm: ")
    if confirm != "CLOSE ALL":
        print("Aborted")
        return

    for pos in open_positions:
        try:
            await ee.force_close(pos, order_type="market")
            print(f"✓ Closed {pos.symbol}")
        except Exception as e:
            print(f"✗ Failed to close {pos.symbol}: {e}")

    await pm.set_system_halted("emergency_manual_halt")
    print("All positions closed. System halted.")

if __name__ == "__main__":
    asyncio.run(main())
```

**运行方式**:

```bash
# 必须在服务器上手动运行
docker compose exec api python scripts/emergency_close_all.py --confirm
```

---

## 八、CI/CD(可选,Phase 1 之后)

### 8.1 GitHub Actions 自动测试

```yaml
# .github/workflows/test.yml
name: Test

on:
  push:
    branches: [main, dev]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Lint
        run: ruff check .

      - name: Type check
        run: mypy core/ strategies/

      - name: Test
        run: pytest tests/

      - name: Coverage
        run: pytest --cov=. --cov-report=xml
```

### 8.2 自动部署(谨慎使用)

```yaml
# 只在 main 分支
deploy:
  runs-on: ubuntu-latest
  needs: test
  if: github.ref == 'refs/heads/main'
  steps:
    - name: SSH deploy
      run: |
        ssh dracula@vps.ip "
          cd Dracula-System &&
          git pull &&
          docker compose pull &&
          docker compose up -d
        "
```

⚠️ **警告**:实盘系统的自动部署有风险——一个 bug 立即上线就可能爆仓。建议:
- 先部署到 staging 环境
- 人工确认后再上 production
- 或者只用 CI 跑测试,部署始终手动

---

## 九、性能调优(后期需要时)

### 9.1 数据库

```sql
-- 监控慢查询
SELECT * FROM pg_stat_statements
ORDER BY total_exec_time DESC LIMIT 10;

-- 检查未使用的索引(占空间)
SELECT * FROM pg_stat_user_indexes WHERE idx_scan = 0;

-- vacuum 清理(每周一次)
VACUUM ANALYZE;
```

### 9.2 Redis

```bash
# 检查内存使用
redis-cli INFO memory

# 慢命令分析
redis-cli SLOWLOG GET 10

# 清理过期 key(自动,但偶尔手动)
redis-cli FLUSHDB  # ⚠️ 慎用,清空当前 db
```

### 9.3 Python 应用

```bash
# 性能分析
python -m cProfile -o profile.out -m core.main
python -m pstats profile.out

# 内存分析
mprof run python -m core.main
mprof plot
```

---

## 十、安全加固清单

```
☐ SSH 用 key 不用密码
☐ SSH 端口改成非 22
☐ fail2ban 防暴力破解
☐ ufw 防火墙开启,只允许必要端口
☐ 自动安全更新开启
☐ Docker 守护进程不暴露 TCP
☐ 数据库/Redis 只 bind 127.0.0.1
☐ Nginx 反代加 IP 白名单 + Basic Auth
☐ HTTPS 证书自动续签
☐ .env 文件 chmod 600
☐ API key 设置 IP 白名单(交易所支持时)
☐ API key 不勾"提币"权限
☐ DEX 钱包用专门的"交易钱包",大额留硬件钱包
☐ 备份到异地 + 加密
☐ Telegram Bot 限定特定 Chat ID
☐ 关闭 GitHub Copilot 训练数据收集
☐ 仓库始终 Private
☐ 定期(每月)用 GitGuardian 扫描泄露
```

---

## 十一、总结

5 句话:

1. **先用小型 VPS,不要本地长跑**——实盘必须 7×24
2. **所有内部端口只 bind 127.0.0.1**,通过 Nginx 反代访问
3. **每天 5 分钟巡检 + 每周 30 分钟回顾 + 每月 2 小时复盘**——这是最低维护成本
4. **应急 Runbook 打印贴墙上**——出事时你不会有时间临时查文档
5. **定期演练备份恢复**——只有"试过能恢复"的备份才是真备份

---

## 你的下一步

✅ **11 篇设计文档全部完成**

接下来我会:

1. 把所有 11 篇文档**合并成一份完整的 PDF**
2. PDF 会有目录、页码、中文字体支持
3. 你下载后可以打印出来,边看边规划

准备生成 PDF。
