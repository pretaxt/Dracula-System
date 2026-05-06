# 09 · 通知矩阵系统 · 完整设计

> **范围**:5 个通知渠道 + 4 个通知级别 + 矩阵化分发
> **目标**:用户**永远不会错过重要事件**,但**也不会被无关通知打扰**

---

## 一、设计原则

### 1.1 分级通知,分级渠道

不是所有事件都要"全渠道轰炸"。我们用**通知矩阵**让用户精确控制。

```
                Telegram  Discord  Email  Toast  系统通知
─────────────────────────────────────────────────────────
INFO 信息          ☐         ☐       ☐      ☑       ☐    用户可调
SUCCESS 成功       ☑         ☐       ☐      ☑       ☐    用户可调
WARN 警告          ☑         ☑       ☐      ☑       ☑    用户可调
CRITICAL 严重     🔒        🔒      🔒     🔒      🔒    强制全开
```

**关键约束**:
- **INFO / SUCCESS / WARN**:用户可任意调整渠道
- **CRITICAL**:**强制所有渠道全开**,不允许关闭(系统熔断、保证金告警这种事不能漏)

### 1.2 防骚扰机制

即使最严重的事件,**同一类型 N 分钟内只发 1 次**。例如:
- 资金费率反转告警:同一币种 30 分钟冷却
- API 错误告警:同一交易所 5 分钟聚合

否则系统会一次发几百条同类型通知,用户烦到关掉 Telegram。

---

## 二、四级事件定义

### 2.1 INFO 级别(信息)

**含义**:背景信息,用户**通常不需要立即看**。

**示例**:
- 新机会被扫到(APR > 10%)
- 资金费已结算到账
- 每日 PnL 总结
- 系统启动/停止

**默认渠道**:仅 Toast(网页弹窗)

**用户调整后**:可以加 Telegram(但会比较吵)

### 2.2 SUCCESS 级别(成功)

**含义**:**用户可能想知道**的好事。

**示例**:
- 建仓成功(收到资金费、Delta 中性建立)
- 平仓完成(锁定收益)
- 跨所对冲成功

**默认渠道**:Telegram + Toast

### 2.3 WARN 级别(警告)

**含义**:**异常但还在控制范围内**,需要用户关注。

**示例**:
- API 延迟过高(超阈值)
- 资金费率即将转负
- 仓位占用率 > 70%
- 单币种回撤超过 3%
- DEX Gas 费异常飙升

**默认渠道**:Telegram + Discord + Toast + 系统通知

### 2.4 CRITICAL 级别(严重)

**含义**:**系统级事件**,可能导致重大损失。

**示例**:
- 单日回撤接近红线(2.5% / 3% 红线)
- 永续保证金率 < 60%
- API 连续失败 3 次(触发熔断)
- 系统熔断已触发(全部仓位强平)
- 检测到异常持仓状态(Delta 严重偏离)

**默认渠道**:**强制全部 5 个渠道**,不可关闭

---

## 三、五个通知渠道

### 3.1 Telegram(推荐主渠道)

**优点**:
- 延迟 < 1 秒
- 跨平台(手机、电脑、Web)
- 免费
- 支持**交互按钮**(用户可一键操作)

**缺点**:
- 需要用户主动创建 Bot
- 中国大陆使用需要 VPN

**实现技术**:
- 用 `python-telegram-bot` 库
- 用户通过 @BotFather 创建 bot,获取 `BOT_TOKEN`
- 用户与 bot 私聊一次,通过 `@userinfobot` 获取 `CHAT_ID`
- 系统用 `BOT_TOKEN` + `CHAT_ID` 推送消息

**消息格式**:

```
🚨 CRITICAL · 单日回撤接近红线

当前回撤:-2.34%
红线:-3.00%
距离红线:0.66%

建议:立即检查持仓状态。系统将在
触及红线时自动平仓所有头寸。

时间:14:21:33 UTC

[查看仪表盘]  [立即平仓]  [我已知晓]
```

**交互按钮**(可选高级功能):
- "立即平仓":点击后机器人会发送确认提示,确认后调用 API 平仓
- "我已知晓":标记已读
- "查看仪表盘":跳转到 Web 界面

⚠️ **安全注意**:交互按钮意味着 Telegram 能调用核心 API。**必须**:
- 确认 chat_id(防止其他人冒充)
- 二次确认(发送验证码)
- 操作日志(谁、什么时候、做了什么)

### 3.2 Discord

**优点**:
- 免费、稳定
- 适合团队场景(多人协作时)
- Webhook 简单,不需要 bot

**缺点**:
- 移动端通知不如 Telegram 及时
- 国内访问受限

**实现**:用户在 Discord 服务器创建 channel → 设置 → 集成 → 创建 Webhook → 复制 URL → 系统用此 URL POST JSON。

```python
import aiohttp

async def send_discord(webhook_url: str, level: str, title: str, message: str):
    color_map = {
        "info": 0x4a9eff,
        "success": 0x00d68f,
        "warn": 0xffb800,
        "critical": 0xff4757
    }
    payload = {
        "embeds": [{
            "title": title,
            "description": message,
            "color": color_map[level],
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    async with aiohttp.ClientSession() as s:
        await s.post(webhook_url, json=payload)
```

### 3.3 Email

**优点**:
- 完整记录(可以归档查询)
- 适合每日总结、月度报告

**缺点**:
- 延迟高(可能几分钟)
- 不适合实时告警

**实现**:推荐 **SendGrid** 或 **Mailgun**(免费层 100 封/天,够用),或者用 SMTP 直接发送。

```python
import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

async def send_email(to: str, subject: str, html_body: str):
    msg = MIMEMultipart('alternative')
    msg['From'] = config.smtp_from
    msg['To'] = to
    msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html'))

    await aiosmtplib.send(
        msg,
        hostname=config.smtp_host,
        port=config.smtp_port,
        username=config.smtp_user,
        password=config.smtp_password,
        use_tls=True
    )
```

### 3.4 Toast(浏览器内弹窗)

**优点**:
- 用户在仪表盘上时立刻看到
- 零成本

**缺点**:
- 用户必须打开仪表盘才能看到

**实现**:WebSocket 推送到前端,前端用 Tailwind + Framer Motion 显示弹窗。

### 3.5 系统通知(浏览器原生 Notification API)

**优点**:
- 即使用户切换到其他标签页也能看到
- 系统级弹窗,引人注意

**缺点**:
- 需要用户授权
- 移动端支持差

**实现**(前端):

```javascript
// 请求权限
if (Notification.permission === 'default') {
    await Notification.requestPermission();
}

// 推送
if (Notification.permission === 'granted') {
    new Notification('🚨 CRITICAL', {
        body: '单日回撤接近红线 -2.34%',
        icon: '/logo.png',
        requireInteraction: true   // 用户不点击不消失
    });
}
```

---

## 四、通知矩阵实现

### 4.1 配置存储

通知矩阵保存在 `user_settings` 表的 `notification_matrix` JSONB 字段:

```json
{
    "info":     {"telegram": false, "discord": false, "email": false, "toast": true,  "system": false},
    "success":  {"telegram": true,  "discord": false, "email": false, "toast": true,  "system": false},
    "warn":     {"telegram": true,  "discord": true,  "email": false, "toast": true,  "system": true},
    "critical": {"telegram": true,  "discord": true,  "email": true,  "toast": true,  "system": true}
}
```

### 4.2 分发逻辑

```python
class NotificationDispatcher:

    async def dispatch(
        self,
        level: str,           # info/success/warn/critical
        title: str,
        message: str,
        metadata: dict = None
    ):
        # 1. 加载用户的通知矩阵
        settings = await self.get_user_settings()
        matrix = settings.notification_matrix

        # 2. CRITICAL 级别强制全开(不允许用户关掉)
        if level == 'critical':
            channels_to_use = ['telegram', 'discord', 'email', 'toast', 'system']
        else:
            channels_to_use = [
                ch for ch, enabled in matrix[level].items() if enabled
            ]

        # 3. 防骚扰检查
        dedup_key = f"{level}:{title}"
        if not self.dedup.should_send(dedup_key):
            return

        # 4. 写入数据库
        notification = await self.repo.create({
            "level": level,
            "title": title,
            "message": message,
            "channels": channels_to_use,
            "metadata": metadata
        })

        # 5. 并发发送到各渠道
        tasks = []
        if 'telegram' in channels_to_use:
            tasks.append(self.telegram.send(level, title, message))
        if 'discord' in channels_to_use:
            tasks.append(self.discord.send(level, title, message))
        if 'email' in channels_to_use:
            tasks.append(self.email.send(level, title, message))
        if 'toast' in channels_to_use:
            tasks.append(self.toast.send(level, title, message))
        if 'system' in channels_to_use:
            tasks.append(self.system.send(level, title, message))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 6. 记录哪些渠道成功
        delivered = []
        for ch, result in zip(channels_to_use, results):
            if not isinstance(result, Exception):
                delivered.append(ch)

        await self.repo.update(notification.id, {"delivered_channels": delivered})
```

### 4.3 防骚扰(Deduplication)

```python
class NotificationDedup:
    """同类通知 N 分钟内只发 1 次"""

    DEDUP_RULES = {
        # 关键字 → 冷却分钟
        "API延迟过高":           5,
        "资金费率反转":           30,
        "Gas费异常":              10,
        "建仓成功":               0,    # 不去重(每次都要发)
        "建仓失败":               0,
        "CRITICAL":              0,    # 严重事件不去重
    }

    def should_send(self, dedup_key: str) -> bool:
        # 用 Redis 记录最近发送时间
        last_sent = redis.get(f"notif:dedup:{dedup_key}")
        if last_sent is None:
            redis.setex(f"notif:dedup:{dedup_key}", cooldown_seconds, NOW)
            return True

        time_since = NOW - last_sent
        cooldown = self._get_cooldown(dedup_key)
        if time_since < cooldown:
            return False

        redis.setex(f"notif:dedup:{dedup_key}", cooldown, NOW)
        return True
```

---

## 五、通知模板

### 5.1 模板系统

每种事件类型有标准化模板,确保信息齐全。

```python
class NotificationTemplate:
    @staticmethod
    def position_opened(position) -> tuple[str, str]:
        title = f"✅ 建仓成功 · {position.symbol}"
        message = f"""
{position.exchange.upper()} · {position.symbol}
建仓金额:${position.notional_usd:.2f}
Delta 中性建立 · 滑点 {position.slippage_pct:.2f}%
当前年化:{position.target_apr_pct:.1f}%
"""
        return title, message

    @staticmethod
    def funding_settled(position, amount) -> tuple[str, str]:
        title = f"💰 资金费已结算"
        message = f"""
{position.symbol} @ {position.exchange.upper()}
收入:+${amount:.2f}
累计今日资金费收入:${position.daily_funding:.2f}
"""
        return title, message

    @staticmethod
    def critical_drawdown(current_dd, limit_dd) -> tuple[str, str]:
        title = f"🚨 CRITICAL · 单日回撤接近红线"
        message = f"""
当前回撤:{current_dd:.2f}%
红线:{limit_dd:.2f}%
距离红线:{abs(limit_dd) - abs(current_dd):.2f}%

建议:立即检查持仓状态。系统将在触及红线时自动平仓所有头寸。
"""
        return title, message
```

### 5.2 多语言支持(可选)

```python
class I18n:
    def __init__(self, lang='zh-CN'):
        self.lang = lang
        self.translations = self._load_translations()

    def t(self, key: str, **kwargs) -> str:
        text = self.translations[self.lang].get(key, key)
        return text.format(**kwargs)
```

Phase 0 阶段只支持中文,简化设计。

---

## 六、Telegram Bot 进阶功能

### 6.1 双向交互

用户可以发送命令给 Bot,获取信息或执行操作:

```
/status         查看系统当前状态
/positions      列出当前持仓
/pnl            查看今日 PnL
/halt           紧急停机(需要密码确认)
/resume         恢复运行
/help           帮助
```

实现:

```python
from telegram.ext import ApplicationBuilder, CommandHandler

async def status_cmd(update, context):
    state = await get_system_state()
    await update.message.reply_text(f"""
📊 系统状态

State: {state.state}
持仓数:{state.position_count}
今日 PnL: ${state.daily_pnl:.2f}
回撤: {state.daily_drawdown:.2f}%
""")

async def halt_cmd(update, context):
    # 二次确认
    await update.message.reply_text("⚠️ 确认停机?发送密码:")
    # 等待下一条消息
    # ...

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("status", status_cmd))
app.add_handler(CommandHandler("halt", halt_cmd))
app.run_polling()
```

### 6.2 安全考虑

⚠️ **Telegram 交互式命令是风险点**——如果 Bot Token 泄露,攻击者能直接发命令操作你的系统。

**强制安全措施**:
- 验证 `chat_id`(只接受预先配置的用户)
- 危险操作(`/halt`、`/resume`、`/close_all`)需要二次密码
- 所有命令记录日志
- Bot Token 在 `.env`,绝不 commit

---

## 七、邮件每日总结

每天凌晨发送一封 HTML 格式的总结邮件:

```html
<h2>📊 Dracula-System 每日总结 · 2026-05-06</h2>

<table>
  <tr><th>指标</th><th>数值</th></tr>
  <tr><td>账户总价值</td><td>$5,247.83</td></tr>
  <tr><td>今日 PnL</td><td style="color: #00d68f">+$23.41 (+0.45%)</td></tr>
  <tr><td>累计 PnL</td><td>$112.56</td></tr>
  <tr><td>当前持仓</td><td>3</td></tr>
  <tr><td>今日资金费收入</td><td>+$8.42</td></tr>
</table>

<h3>持仓详情</h3>
...

<h3>风控状态</h3>
全部 5 项指标 安全 ✅

<h3>明日机会预览</h3>
- ENA/USDT @ Bybit:34.2% APR
- HYPE/USDC @ Hyperliquid:46.1% APR

<a href="https://your-dashboard.com">查看仪表盘</a>
```

---

## 八、配置文件

```yaml
# config/notifications.yaml

dispatch:
  enable_dedup: true
  default_cooldown_minutes: 5

channels:
  telegram:
    enabled: true
    bot_token: ${TELEGRAM_BOT_TOKEN}
    chat_id: ${TELEGRAM_CHAT_ID}
    enable_interactive: true       # 交互式命令
    interactive_password: ${TG_INTERACTIVE_PASSWORD}

  discord:
    enabled: true
    webhook_url: ${DISCORD_WEBHOOK_URL}

  email:
    enabled: true
    smtp_host: ${SMTP_HOST}
    smtp_port: ${SMTP_PORT}
    smtp_user: ${SMTP_USER}
    smtp_password: ${SMTP_PASSWORD}
    from_email: ${SMTP_FROM_EMAIL}
    to_email: ${SMTP_TO_EMAIL}
    daily_summary_time: "00:00"  # UTC

  toast:
    enabled: true
    persist_seconds: 5

  system:
    enabled: true
    require_user_permission: true

# 默认通知矩阵(用户可在 UI 改)
default_matrix:
  info:
    telegram: false
    discord: false
    email: false
    toast: true
    system: false
  success:
    telegram: true
    discord: false
    email: false
    toast: true
    system: false
  warn:
    telegram: true
    discord: true
    email: false
    toast: true
    system: true
  critical:
    telegram: true     # 强制
    discord: true      # 强制
    email: true        # 强制
    toast: true        # 强制
    system: true       # 强制

# 防骚扰规则
dedup_rules:
  "API延迟过高": 5
  "资金费率反转": 30
  "Gas费异常": 10
  "WebSocket断连": 1
  "保证金率告警": 5
```

---

## 九、目录结构

```
notifications/
├── __init__.py
├── dispatcher.py             -- 通知分发器
├── dedup.py                  -- 去重逻辑
├── matrix.py                 -- 矩阵管理
├── templates.py              -- 通知模板
├── i18n.py                   -- 多语言(预留)
├── channels/
│   ├── __init__.py
│   ├── base.py               -- 渠道基类
│   ├── telegram.py
│   ├── telegram_interactive.py  -- Bot 命令处理
│   ├── discord.py
│   ├── email.py
│   ├── toast.py
│   └── system.py
└── reports/
    ├── daily_summary.py      -- 每日总结邮件
    └── weekly_summary.py     -- 周总结
```

---

## 十、测试要求

### 10.1 单元测试

每个渠道:
- ✅ 模拟 API 成功响应
- ✅ 模拟 API 失败响应(网络错误、认证错误)
- ✅ 模板渲染正确

### 10.2 集成测试

- ☐ 实际发送一条到 Telegram(测试 chat)
- ☐ 实际发送一条到 Discord(测试 channel)
- ☐ 实际发送一封到 Email
- ☐ 验证 dedup 在 Redis 中正确工作
- ☐ 验证 CRITICAL 级别强制全渠道

### 10.3 用户接受测试

- ☐ 用户能在 UI 改通知矩阵
- ☐ 用户能配置自己的 Telegram Bot
- ☐ 用户能配置自己的 Discord Webhook
- ☐ 用户能收到测试通知

---

## 十一、总结

5 句话:

1. **5 个渠道(Telegram / Discord / Email / Toast / 系统通知) × 4 级别(INFO / SUCCESS / WARN / CRITICAL)**
2. **CRITICAL 强制全开**,其他级别用户可调
3. **防骚扰**:同类型通知有冷却时间,避免轰炸
4. **Telegram 交互式**:可一键查询、紧急停机
5. **每日邮件总结**:好的复盘工具

---

## 你的下一步

1. 接下来第 10 篇:回测框架
2. 然后第 11 篇:部署 + 应急手册,然后合并 PDF
