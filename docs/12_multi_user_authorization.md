# 12 · 多用户授权层(白名单模式)· 完整设计

> **范围**:Beta 版多用户支持 + API key 安全存储 + 用户隔离
> **优先级**:🟡 P2(核心系统稳定后实施)
> **目标**:让你认识的 3-10 个朋友能用同一套部署的系统,**且不承担法律风险**
> **明确不做**:支付、订阅、公开注册、客服、KYC

---

## 🔄 Review v1.1 更新(2026-05-06)

> **用户(老虎)review 反馈**:"我需要多个账户同步参与策略研究"(指家人/朋友)
> 
> **决策**:把这一章的内容**拆成两个阶段**:

### Phase 1 · 双用户家人版(0-6 月)

```
范围:你 + 1-2 个家人(共 2-3 人)
状态:Phase 1 启用(原本要等 Phase 2 才做)
目标:满足"家人账户参与策略研究"诉求

核心特性:
✅ 用户认证 + 角色(super_admin / user)
✅ API key 三层加密存储(本章 §3 完整实现)
✅ Per-user 资金 / 持仓 / 订单隔离
✅ 简化 UI:可切换查看不同人的数据(只读跨账户)

简化省略:
❌ 邀请码(2-3 个人手动 INSERT 即可)
❌ 完整用户管理 UI
❌ 公开 API
❌ 复杂权限系统

工作量:1 个月(大约第 16-20 周)
```

### Phase 2 · 多用户朋友版(6-12 月)

```
范围:你 + 5-10 个朋友
状态:核心系统稳定 3 个月后才做
目标:正式开放给可信社群

核心特性(在 Phase 1 基础上加):
✅ 邀请码系统
✅ 完整用户管理 UI
✅ 用户独立部署模式(可选)
✅ 详细 audit log

工作量:1 个月(大约第 36-40 周)
```

### 为什么这样拆

| 考虑 | 单步做(原 v1.0) | 拆两步(v1.1) |
|---|---|---|
| 满足家人诉求时间 | 第 36+ 周 | **第 16-20 周** ✅ 提早 16 周 |
| 总工作量 | 2 个月 | 2 个月(同) |
| 风险 | 一次性全做,bug 风险大 | 渐进,每步验证 |
| 法律风险 | 同 | 同(都是白名单) |

**关键保留**:**所有"白名单 + 完全免费"原则不变**(决策 5.1)。Phase 1 双用户和 Phase 2 多用户都不收费、不公开、不做客服。

---

## 序言:这一章的边界(以下为 v1.0 原内容,适用于 Phase 2)

### 0.1 这是 "Beta 白名单模式",不是 "SaaS"

**性质**:
- ✅ 你 = 系统运营者
- ✅ 用户 = 你认识的人(家人、朋友、可信开发者)
- ✅ 加入需要你**手动**发邀请码
- ✅ **完全免费**,不收任何形式的费用
- ✅ 无 SLA、无客服、无退款机制

**为什么这样设计**:
- 不收钱 → 不构成"金融服务" → 不需要金融牌照
- 邀请制 → 不构成"公开服务" → 大幅降低法律风险
- 用户都是认识的人 → 出问题能直接沟通,不会上法庭

**未来想商业化时**:
- 这一层架构**完全保留**,只需要加支付集成、KYC、客服三个模块
- 也就是说,**做这一层不是浪费**,是为未来铺路

### 0.2 用户必须签的"友情声明"(不是 ToS)

每个用户加入时,**必须签**一份简短声明(电子签名 / 文字承认即可),内容大致:

```
我 ______ 自愿使用 _____ 提供的 Dracula-System 软件。

我理解并承诺:
1. 这是一个朋友间的免费分享,不是商业服务。
2. 任何资金损失我自己承担,不向 _____ 索赔。
3. 我使用我自己的交易所账户,自己生成的 API key。
4. 我可以随时退出,删除我的账户和数据。
5. 我承诺不将此系统转交给陌生人,不进行任何形式的转售。

签名:______
日期:______
```

**这不是法律意义上的免责**(免责条款无法 100% 免责),而是:
- 让用户**意识到风险**,不会以为这是"专业产品"
- 强调这是"朋友分享",不是商业关系
- 万一出问题,这是"朋友间约定",不是"消费纠纷"

### 0.3 明确的不做清单

**绝对不做**(法律风险):
- ❌ 接受任何形式的付款(USDT、银行转账、加密货币、礼物卡都不行)
- ❌ 让任何不认识的人注册
- ❌ 在公开渠道宣传(Twitter / Reddit / 微博 都不行)
- ❌ 提供客服 / 投诉处理流程
- ❌ 承诺任何收益或 SLA

**暂时不做**(未来需要时再加):
- 支付集成
- 公开注册
- KYC / AML
- 多角色权限(只有超管 + 普通用户两种)
- 用户分组 / 团队
- 多语言

---

## 一、整体架构变化

### 1.1 单租户 vs Beta 多租户的对比

```
原架构(11 份文档描述的):
┌──────────────────────────────────────────┐
│  你 ──→ Dracula-System ──→ 你的交易所    │
│         (单一用户上下文)                │
└──────────────────────────────────────────┘

新架构(本文档):
┌──────────────────────────────────────────┐
│  你 ───┐                                  │
│  朋友A ┼→ Dracula-System ─┬→ 你的交易所  │
│  朋友B ┘  (多用户上下文)  ├→ A 的交易所  │
│                            └→ B 的交易所  │
│                                           │
│  超管面板(只有你能看)──→ 监控所有用户 │
└──────────────────────────────────────────┘
```

### 1.2 影响的模块

| 模块 | 改动量 | 优先级 |
|---|---|---|
| 数据库 schema | 大(几乎所有表加 user_id) | P0 |
| API 层 | 中(每个 endpoint 加用户验证) | P0 |
| 认证 / 授权 | 全新 | P0 |
| 加密 key 存储 | 全新 | P0 |
| 策略引擎 | 中(按用户加载配置和 API key) | P0 |
| 通知系统 | 小(notifications 加 user_id) | P1 |
| 风控 | 中(用户级 + 系统级) | P1 |
| 前端 | 中(登录、用户切换) | P1 |
| 超管面板 | 全新 | P2 |

---

## 二、用户模型

### 2.1 数据库:users 表

```sql
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL UNIQUE,

    -- 身份
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,

    -- 认证
    password_hash VARCHAR(255) NOT NULL,         -- bcrypt
    password_salt VARCHAR(64),
    last_password_change TIMESTAMPTZ,

    -- 2FA(强烈建议每个用户开启)
    totp_secret_encrypted TEXT,                  -- 用主密钥加密
    totp_enabled BOOLEAN DEFAULT FALSE,
    backup_codes_encrypted TEXT,                 -- 备用码,主密钥加密

    -- 角色
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    -- super_admin: 你自己,只有 1 个
    -- user:        普通用户

    -- 状态
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    -- active:     正常使用
    -- suspended:  超管暂停(策略停止,数据保留)
    -- pending:    刚注册待激活
    -- archived:   已退出(数据按用户要求保留或删除)

    -- 加入信息
    invited_by BIGINT REFERENCES users(id),
    invitation_code_used VARCHAR(32),
    joined_at TIMESTAMPTZ DEFAULT NOW(),

    -- 友情声明签署
    agreement_signed_at TIMESTAMPTZ,
    agreement_version VARCHAR(10),              -- v1.0 / v1.1...
    agreement_text TEXT,                        -- 完整签署内容备份

    -- 资金
    total_capital_usd DECIMAL(20, 2) DEFAULT 0,

    -- 元数据
    last_login_at TIMESTAMPTZ,
    last_login_ip INET,
    failed_login_count INT DEFAULT 0,

    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_users_status ON users(status);
CREATE INDEX idx_users_role ON users(role);

-- 强制只能有 1 个 super_admin
CREATE UNIQUE INDEX idx_only_one_super_admin
    ON users(role) WHERE role = 'super_admin';
```

### 2.2 邀请码:invitations 表

```sql
CREATE TABLE invitations (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(32) NOT NULL UNIQUE,            -- 邀请码,如 "DRACULA-XYZ123"

    issued_by BIGINT NOT NULL REFERENCES users(id),
    issued_to_email VARCHAR(255),                -- 可选,限定邮箱

    expires_at TIMESTAMPTZ NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    used_at TIMESTAMPTZ,
    used_by BIGINT REFERENCES users(id),

    notes TEXT,                                  -- "给小张的"

    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 2.3 角色定义

**super_admin**(就是你):
- 可看所有用户的状态(持仓、PnL、风控)
- 可暂停 / 恢复用户
- 可发邀请码
- 不能看任何用户的 API key 明文(技术上能,但日志会记录)
- 不能代用户下单
- 不能修改用户的密码 / 2FA(只能让用户自己重置)

**user**(朋友):
- 只能看自己的数据
- 完全控制自己的策略和 API key
- 可以随时导出自己的数据 + 删除账户
- 不能看其他用户的任何信息

---

## 三、API key 加密存储(最重要的部分)

### 3.1 加密架构

```
┌──────────────────────────────────────────────────┐
│ 主密钥(Master Key, MEK)                        │
│   - 32 字节随机数                                │
│   - 存在 /etc/dracula/master.key,chmod 600     │
│   - 不进数据库,不进 git                         │
│   - 系统启动时加载到内存                         │
└────────────────────┬─────────────────────────────┘
                     │ 用 MEK 加密
                     ▼
┌──────────────────────────────────────────────────┐
│ 数据加密密钥(DEK)                              │
│   - 每个用户独立的 DEK                           │
│   - 存数据库的 user_secrets.dek_encrypted       │
│   - DEK 本身用 MEK 加密后存储                    │
└────────────────────┬─────────────────────────────┘
                     │ 用 DEK 加密
                     ▼
┌──────────────────────────────────────────────────┐
│ 用户的 API key                                   │
│   - 用 DEK 加密                                  │
│   - 存 exchange_credentials.api_secret_encrypted│
└──────────────────────────────────────────────────┘

层级关系:
  master.key 解密 → user.dek 解密 → user.api_secret 明文
```

**为什么三层而不是两层**:
- 想轮换 MEK 时:只需要重新加密所有用户的 DEK,不需要重新加密所有 API secret
- 用户被删除时:只需要删除该用户的 DEK,该用户所有 API secret 自动不可解密
- 隔离性强:你看到的是密文,即使数据库泄露,key 也不可用

### 3.2 数据库:用户密钥和凭证

```sql
-- 用户的数据加密密钥(DEK)
CREATE TABLE user_secrets (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    -- DEK,用 MEK 加密
    dek_encrypted BYTEA NOT NULL,
    dek_version INT DEFAULT 1,                  -- 用于轮换

    -- 元数据
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

-- 用户的交易所 API key
CREATE TABLE exchange_credentials (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL UNIQUE,

    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    exchange VARCHAR(30) NOT NULL,              -- binance/bybit/...
    instrument_types TEXT[] NOT NULL,           -- [spot, perpetual]

    -- 加密的凭证
    api_key_encrypted BYTEA NOT NULL,
    api_secret_encrypted BYTEA NOT NULL,
    api_passphrase_encrypted BYTEA,             -- OKX/Bitget 需要

    -- DEX 钱包
    wallet_address VARCHAR(100),                -- 公开,不需要加密
    wallet_private_key_encrypted BYTEA,         -- 私钥加密

    -- 元数据
    label VARCHAR(100),                         -- "我的 Binance 主账户"
    enabled BOOLEAN DEFAULT TRUE,

    -- 安全审计
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    last_decrypted_at TIMESTAMPTZ,
    decryption_count INT DEFAULT 0,

    -- 已知信息(用户输入时验证后保存,加密 key 失效时还能看出是哪个 key)
    api_key_last_4 VARCHAR(4),                  -- "...AB12"
    api_key_first_4 VARCHAR(4),                 -- "CD34..."

    UNIQUE (user_id, exchange, label)
);

CREATE INDEX idx_credentials_user ON exchange_credentials(user_id);
CREATE INDEX idx_credentials_enabled ON exchange_credentials(user_id, enabled);
```

### 3.3 加密代码示例

```python
import os
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class EncryptionService:
    """三层密钥加密服务"""

    def __init__(self, master_key_path: str = "/etc/dracula/master.key"):
        self._master_key = self._load_master_key(master_key_path)

    def _load_master_key(self, path: str) -> bytes:
        """加载主密钥(只在内存里)"""
        if not os.path.exists(path):
            raise RuntimeError(
                f"Master key not found at {path}. "
                f"Run scripts/generate_master_key.py first."
            )
        with open(path, "rb") as f:
            key = f.read()
        if len(key) != 32:
            raise RuntimeError("Master key must be exactly 32 bytes")
        return key

    def generate_dek(self) -> bytes:
        """为新用户生成 DEK"""
        return Fernet.generate_key()

    def encrypt_dek(self, dek: bytes) -> bytes:
        """用 MEK 加密 DEK"""
        f = Fernet(self._derive_fernet_key(self._master_key))
        return f.encrypt(dek)

    def decrypt_dek(self, dek_encrypted: bytes) -> bytes:
        """用 MEK 解密 DEK"""
        f = Fernet(self._derive_fernet_key(self._master_key))
        return f.decrypt(dek_encrypted)

    def encrypt_data(self, data: str, dek: bytes) -> bytes:
        """用 DEK 加密数据"""
        f = Fernet(dek)
        return f.encrypt(data.encode())

    def decrypt_data(self, data_encrypted: bytes, dek: bytes) -> str:
        """用 DEK 解密数据"""
        f = Fernet(dek)
        return f.decrypt(data_encrypted).decode()

    def _derive_fernet_key(self, key_material: bytes) -> bytes:
        """从 32 字节密钥派生 Fernet 兼容的密钥"""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"dracula-mek-derivation"
        )
        derived = hkdf.derive(key_material)
        # Fernet 需要 base64-encoded 32 bytes
        import base64
        return base64.urlsafe_b64encode(derived)


class CredentialService:
    """API key 加解密服务"""

    def __init__(self, encryption: EncryptionService, db):
        self.enc = encryption
        self.db = db
        self._dek_cache: dict[int, bytes] = {}

    async def add_credential(
        self,
        user_id: int,
        exchange: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str = None,
        label: str = "default"
    ):
        """添加用户的交易所凭证"""

        # 1. 获取或创建用户的 DEK
        dek = await self._get_user_dek(user_id)

        # 2. 加密
        api_key_enc = self.enc.encrypt_data(api_key, dek)
        api_secret_enc = self.enc.encrypt_data(api_secret, dek)
        api_passphrase_enc = (
            self.enc.encrypt_data(api_passphrase, dek)
            if api_passphrase else None
        )

        # 3. 保存(明文 API key 不进数据库)
        await self.db.exchange_credentials.insert({
            "user_id": user_id,
            "exchange": exchange,
            "api_key_encrypted": api_key_enc,
            "api_secret_encrypted": api_secret_enc,
            "api_passphrase_encrypted": api_passphrase_enc,
            "api_key_first_4": api_key[:4],
            "api_key_last_4": api_key[-4:],
            "label": label,
        })

        # 4. 立刻清明文(防止内存 dump 泄露)
        del api_key, api_secret, api_passphrase

    async def get_credential_for_use(
        self, user_id: int, exchange: str, label: str = "default"
    ) -> dict:
        """获取明文凭证用于实际下单(谨慎使用!)"""

        # 1. 获取用户的 DEK
        dek = await self._get_user_dek(user_id)

        # 2. 从数据库读加密的凭证
        cred = await self.db.exchange_credentials.find_one(
            user_id=user_id, exchange=exchange, label=label
        )

        if not cred or not cred["enabled"]:
            raise CredentialNotFoundError()

        # 3. 解密
        api_key = self.enc.decrypt_data(cred["api_key_encrypted"], dek)
        api_secret = self.enc.decrypt_data(cred["api_secret_encrypted"], dek)
        api_passphrase = (
            self.enc.decrypt_data(cred["api_passphrase_encrypted"], dek)
            if cred["api_passphrase_encrypted"] else None
        )

        # 4. 审计日志(谁、什么时候、解密了哪个 key)
        await self._log_decryption(user_id, exchange, label)

        # 5. 更新使用记录
        await self.db.exchange_credentials.update(
            id=cred["id"],
            last_used_at=now(),
            last_decrypted_at=now(),
            decryption_count=cred["decryption_count"] + 1
        )

        return {
            "api_key": api_key,
            "api_secret": api_secret,
            "api_passphrase": api_passphrase,
        }

    async def _get_user_dek(self, user_id: int) -> bytes:
        """获取用户的 DEK(带缓存,降低主密钥使用频率)"""

        if user_id in self._dek_cache:
            return self._dek_cache[user_id]

        secret = await self.db.user_secrets.find_one(user_id=user_id)
        if not secret:
            # 第一次使用,创建 DEK
            dek = self.enc.generate_dek()
            dek_enc = self.enc.encrypt_dek(dek)
            await self.db.user_secrets.insert({
                "user_id": user_id,
                "dek_encrypted": dek_enc,
            })
        else:
            dek = self.enc.decrypt_dek(secret["dek_encrypted"])

        # 缓存(进程内存,不持久化)
        self._dek_cache[user_id] = dek
        return dek

    async def _log_decryption(self, user_id: int, exchange: str, label: str):
        """记录每次解密(用于审计)"""
        await self.db.audit_log.insert({
            "event_type": "credential_decrypted",
            "user_id": user_id,
            "details": {
                "exchange": exchange,
                "label": label,
            },
            "actor": "system",        # 系统自动解密(策略运行时)
        })
```

### 3.4 主密钥管理

#### 生成主密钥

```python
# scripts/generate_master_key.py

import os
import secrets

MASTER_KEY_PATH = "/etc/dracula/master.key"

def generate():
    if os.path.exists(MASTER_KEY_PATH):
        print(f"❌ Master key already exists at {MASTER_KEY_PATH}")
        print("If you want to regenerate, you need to:")
        print("  1. Backup the old key (everyone's data will be unreadable)")
        print("  2. Delete the old key")
        print("  3. Run this script again")
        print("  4. Re-enter all API keys for all users")
        return

    os.makedirs(os.path.dirname(MASTER_KEY_PATH), exist_ok=True)
    key = secrets.token_bytes(32)
    with open(MASTER_KEY_PATH, "wb") as f:
        f.write(key)
    os.chmod(MASTER_KEY_PATH, 0o600)
    print(f"✅ Master key generated at {MASTER_KEY_PATH}")
    print(f"   Permissions: 0600")
    print()
    print("⚠️  CRITICAL: Back up this file to a safe place RIGHT NOW.")
    print("    If you lose it, ALL API KEYS BECOME UNRECOVERABLE.")

if __name__ == "__main__":
    generate()
```

#### 主密钥备份策略

**至少 2 处备份**(分散风险):
1. 你自己的硬件钱包备份介质(Ledger 配套的 nano)
2. 云备份(加密后传到 Google Drive / iCloud)

**备份格式建议**:
```bash
# 加密备份
gpg --symmetric --cipher-algo AES256 /etc/dracula/master.key
# 输入一个超强密码(20+ 位,不重复使用)
# 生成 master.key.gpg

# 上传 master.key.gpg 到云盘
# 删除本地的 .gpg 文件(不要存)

# 恢复时:
# 下载 master.key.gpg → gpg --decrypt → 输入密码 → 得到 master.key
```

#### 主密钥轮换

```python
# scripts/rotate_master_key.py

async def rotate_master_key(old_key: bytes, new_key: bytes):
    """轮换主密钥:重新加密所有用户的 DEK"""
    for secret in db.user_secrets.find_all():
        # 用旧 key 解密 DEK
        old_enc = EncryptionService.with_key(old_key)
        dek = old_enc.decrypt_dek(secret["dek_encrypted"])

        # 用新 key 加密 DEK
        new_enc = EncryptionService.with_key(new_key)
        dek_new_enc = new_enc.encrypt_dek(dek)

        await db.user_secrets.update(
            user_id=secret["user_id"],
            dek_encrypted=dek_new_enc,
            dek_version=secret["dek_version"] + 1
        )

    print(f"✅ Rotated {count} user DEKs")
```

**建议每 12 个月轮换一次**。

---

## 四、用户隔离

### 4.1 数据库行级隔离

**所有事务表都加 `user_id` 外键**:

```sql
-- 修改 strategy_instances 表(在 08 文档基础上)
ALTER TABLE strategy_instances
    ADD COLUMN user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX idx_strategy_instances_user ON strategy_instances(user_id);

-- 修改 positions 表
ALTER TABLE positions
    ADD COLUMN user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX idx_positions_user ON positions(user_id);

-- 同样改 position_legs / orders / opportunities / risk_events / notifications
-- 全部加 user_id

-- 时序表也要(price_history、funding_rate_history 例外,因为是公共数据)
ALTER TABLE pnl_timeseries
    ADD COLUMN user_id BIGINT;
CREATE INDEX idx_pnl_timeseries_user ON pnl_timeseries(user_id, time DESC);
```

### 4.2 PostgreSQL Row-Level Security(RLS)

**强制数据库层面隔离**——即使应用层代码忘了过滤 user_id,数据库也会拦住。

```sql
-- 启用 RLS
ALTER TABLE positions ENABLE ROW LEVEL SECURITY;

-- 创建策略:用户只能看到自己的行
CREATE POLICY user_isolation_select ON positions
    FOR SELECT
    USING (user_id = current_setting('app.current_user_id')::BIGINT);

CREATE POLICY user_isolation_insert ON positions
    FOR INSERT
    WITH CHECK (user_id = current_setting('app.current_user_id')::BIGINT);

CREATE POLICY user_isolation_update ON positions
    FOR UPDATE
    USING (user_id = current_setting('app.current_user_id')::BIGINT);

CREATE POLICY user_isolation_delete ON positions
    FOR DELETE
    USING (user_id = current_setting('app.current_user_id')::BIGINT);

-- 超管绕过 RLS(查询前先 SET role)
CREATE POLICY super_admin_all ON positions
    FOR ALL
    TO super_admin_role
    USING (TRUE);
```

**应用层使用**:

```python
async def with_user_context(user_id: int):
    """为当前请求设置用户上下文"""
    await db.execute(
        f"SET LOCAL app.current_user_id = '{user_id}'"
    )
    # 此后所有查询自动过滤 user_id

# FastAPI 中间件
@app.middleware("http")
async def set_user_context(request, call_next):
    user_id = await authenticate(request)
    async with db.transaction():
        await with_user_context(user_id)
        response = await call_next(request)
    return response
```

### 4.3 API 层隔离

```python
from fastapi import Depends, HTTPException

async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """从 JWT 提取用户"""
    user = await verify_jwt(token)
    if not user:
        raise HTTPException(401, "Unauthorized")
    if user.status != "active":
        raise HTTPException(403, "Account suspended")
    return user

async def require_super_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "super_admin":
        raise HTTPException(403, "Super admin only")
    return user

# 普通用户 endpoint
@app.get("/api/positions")
async def list_my_positions(user: User = Depends(get_current_user)):
    return await db.positions.find(user_id=user.id)

# 超管 endpoint
@app.get("/api/admin/users")
async def list_all_users(admin: User = Depends(require_super_admin)):
    return await db.users.find_all()
```

---

## 五、注册流程(邀请制)

### 5.1 你发邀请码

```python
# 你在超管面板上点 "生成邀请码"
async def create_invitation(
    admin_user: User,
    notes: str = None,
    expires_in_days: int = 7,
    issued_to_email: str = None
) -> str:
    """生成 7 天有效邀请码"""

    code = f"DRACULA-{secrets.token_urlsafe(8).upper()}"

    await db.invitations.insert({
        "code": code,
        "issued_by": admin_user.id,
        "issued_to_email": issued_to_email,
        "expires_at": now() + timedelta(days=expires_in_days),
        "notes": notes,
    })

    return code   # 你用 Telegram / 微信 把码发给朋友
```

### 5.2 朋友用邀请码注册

```python
@app.post("/api/auth/register")
async def register(data: RegisterRequest):
    # 1. 验证邀请码
    invitation = await db.invitations.find_one(code=data.invitation_code)
    if not invitation:
        raise HTTPException(400, "Invalid invitation code")
    if invitation.used:
        raise HTTPException(400, "Invitation code already used")
    if invitation.expires_at < now():
        raise HTTPException(400, "Invitation code expired")
    if invitation.issued_to_email and invitation.issued_to_email != data.email:
        raise HTTPException(400, "Invitation code is for a different email")

    # 2. 验证用户名 / 邮箱不冲突
    if await db.users.find_one(username=data.username):
        raise HTTPException(400, "Username taken")
    if await db.users.find_one(email=data.email):
        raise HTTPException(400, "Email already registered")

    # 3. 验证签了友情声明
    if not data.agreement_signed:
        raise HTTPException(400, "Must sign the agreement")

    # 4. 创建用户
    user = await db.users.insert({
        "username": data.username,
        "email": data.email,
        "password_hash": bcrypt.hash(data.password),
        "role": "user",
        "status": "active",
        "invited_by": invitation.issued_by,
        "invitation_code_used": data.invitation_code,
        "agreement_signed_at": now(),
        "agreement_version": "v1.0",
        "agreement_text": AGREEMENT_TEXT_V1_0,
    })

    # 5. 标记邀请码已使用
    await db.invitations.update(
        code=data.invitation_code,
        used=True,
        used_at=now(),
        used_by=user.id
    )

    # 6. 提醒用户配置 2FA
    return {
        "user_id": user.id,
        "next_steps": [
            "Set up 2FA",
            "Add exchange API keys",
            "Configure your strategies"
        ]
    }
```

### 5.3 友情声明文本(v1.0)

```python
AGREEMENT_TEXT_V1_0 = """
Dracula-System 友情使用声明 v1.0

我自愿使用 Dracula-System 系统(以下简称"系统"),理解以下事项:

1. 这是一个免费的私人分享,不是商业产品。
   系统的运营者(以下简称"运营者")没有从我这里收取任何费用。

2. 系统的目的是辅助加密货币量化交易。
   加密货币交易具有极高风险,我可能损失全部投入资金。

3. 我使用我自己的交易所账户和我自己生成的 API key。
   运营者不持有、不管理我的资金。
   任何资金损失由我自行承担,我不向运营者索赔。

4. 我理解系统可能存在 bug、网络中断、交易所故障等问题。
   我已经知道,做加密货币量化交易,亏损是正常的。

5. 我承诺不将系统转交给陌生人,不进行任何形式的转售。

6. 我可以随时退出。退出时我可以要求删除我的所有数据。

7. 运营者保留随时停止服务、暂停我的账户的权利,无需赔偿。

签署即表示我已阅读并同意以上内容。

------
签署人:[username]
签署时间:[timestamp]
邀请人:[invited_by_username]
"""
```

---

## 六、用户级配置

### 6.1 每个用户独立的策略配置

```sql
-- 把 strategy_instances 表的配置加 user_id
-- 已经在第 4 节加过了

-- 每个用户可以启用 / 禁用自己的策略实例,
-- 配置自己的资金分配,自己的风控阈值
```

**示例**:
- 用户 A 启用 funding_rate_arb,资金 $2000
- 用户 B 也启用 funding_rate_arb,资金 $5000
- 系统看到的是**两个独立的策略实例**,各跑各的

### 6.2 用户级风控

每个用户独立的 Tier 1/2/3 风控:

```python
# core/risk/risk_engine.py

class RiskEngine:
    async def check_user_risk(self, user_id: int, action: TradingAction):
        # 1. 检查用户级风控
        user_risk = await self.get_user_risk_state(user_id)

        if user_risk.daily_drawdown > user_risk.tier3_drawdown_limit:
            await self.halt_user(user_id, "Tier 3: daily drawdown limit")
            return False

        # 2. 检查系统级风控(防止单个用户拖垮整个系统)
        system_risk = await self.get_system_risk_state()
        if system_risk.api_error_rate > 0.1:
            return False  # 系统级问题,所有用户都暂停

        return True

    async def halt_user(self, user_id: int, reason: str):
        """暂停某用户的所有策略,平掉所有持仓"""
        # 1. 标记用户状态
        await db.users.update(id=user_id, status="risk_halted")

        # 2. 平掉该用户所有持仓
        positions = await db.positions.find(user_id=user_id, status="open")
        for pos in positions:
            await self.close_position(pos)

        # 3. 通知用户
        await self.notify_user(user_id, "CRITICAL", "您的账户已触发风控暂停", reason)

        # 4. 通知超管
        await self.notify_super_admin("User halted", user_id, reason)
```

### 6.3 用户级通知

第 9 章的通知矩阵改为**每用户独立**:

```sql
ALTER TABLE user_settings ADD COLUMN user_id BIGINT REFERENCES users(id);
-- 每个用户有自己的 notification_matrix
-- 每个用户配自己的 Telegram bot、Discord webhook、Email
```

---

## 七、超管面板

### 7.1 你能看的内容

```
仪表盘 - 系统总览
  - 用户数:5 (active) / 1 (suspended)
  - 系统状态:RUNNING
  - 全部用户总持仓数:12
  - 全部用户今日总 PnL:+$234

用户列表
  - username | role | status | 持仓 | 今日 PnL | 操作
  - 你 | super_admin | active | 3 | +$45 | -
  - 朋友A | user | active | 2 | +$23 | [暂停] [查看]
  - 朋友B | user | active | 4 | +$89 | [暂停] [查看]
  - ...

风控告警(全用户)
  - 朋友B 触发 Tier 2 警告
  - ...

审计日志
  - [12:34] system 解密了 user_5 的 binance API key
  - [12:35] super_admin 查看了 user_5 的持仓
  - [12:40] user_5 修改了风控阈值
  - ...
```

### 7.2 你不能做的事(技术上限制)

**这些是设计原则**,不是说你"不应该",而是**代码里写死了**:

```python
# 超管不能查看用户 API key 明文
@app.get("/api/admin/users/{user_id}/credentials")
async def admin_view_credentials(
    user_id: int,
    admin: User = Depends(require_super_admin)
):
    creds = await db.exchange_credentials.find(user_id=user_id)
    # 只返回元数据,不解密
    return [
        {
            "exchange": c["exchange"],
            "label": c["label"],
            "api_key_first_4": c["api_key_first_4"],
            "api_key_last_4": c["api_key_last_4"],
            "enabled": c["enabled"],
            "created_at": c["created_at"],
            "last_used_at": c["last_used_at"],
        }
        for c in creds
    ]
    # ⚠️ 没有 API endpoint 能解密 user 的 API key
    # 解密只在策略引擎运行时自动发生,且全部记日志

# 超管不能代用户下单
# (没有 endpoint 能让超管以用户身份调用交易所 API)

# 超管不能修改用户密码
# (用户密码哈希是不可逆的,即使你能改 password_hash,你也不知道用户的明文密码)
```

### 7.3 但你能做的"重要操作"

```python
# 暂停用户(紧急情况,如发现某用户疯狂亏损)
@app.post("/api/admin/users/{user_id}/suspend")
async def suspend_user(
    user_id: int,
    reason: str,
    admin: User = Depends(require_super_admin)
):
    # 平仓所有持仓
    await close_all_positions(user_id)

    # 标记暂停
    await db.users.update(id=user_id, status="suspended")

    # 通知用户
    await notify_user(user_id, "CRITICAL", "您的账户已被超管暂停", reason)

    # 审计日志
    await audit_log(admin.id, "suspend_user", user_id, reason)

# 删除用户(用户主动要求 + 数据 GDPR-style 删除)
@app.delete("/api/admin/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: User = Depends(require_super_admin)
):
    # 1. 平掉所有持仓
    await close_all_positions(user_id)

    # 2. 删除 DEK(导致所有该用户的 API key 不可解密)
    await db.user_secrets.delete(user_id=user_id)

    # 3. 软删除用户(保留审计记录)
    await db.users.update(
        id=user_id,
        status="archived",
        username=f"deleted_user_{user_id}",
        email=f"deleted_{user_id}@deleted",
    )
```

---

## 八、用户的"自我服务"功能

每个用户能做的事:

### 8.1 配置自己的交易所 API key

```python
@app.post("/api/credentials")
async def add_credential(
    data: CredentialCreate,
    user: User = Depends(get_current_user)
):
    await credential_service.add_credential(
        user_id=user.id,
        exchange=data.exchange,
        api_key=data.api_key,
        api_secret=data.api_secret,
        api_passphrase=data.api_passphrase,
        label=data.label
    )
    # 立即测试连通性
    await test_connection(user.id, data.exchange, data.label)
```

**前端 UI 提示**:

```
添加 Binance API Key

⚠️ 强制提醒:
- API key 必须关闭"提币"权限
- API key 必须设置 IP 白名单(本系统的服务器 IP:x.x.x.x)
- 如果你不知道这两步怎么做,先看视频教程,再回来填

[ ] 我已经关闭了提币权限
[ ] 我已经设置了 IP 白名单

API Key:        ____________________
API Secret:     ____________________
Label:          [ 主账户 ]

[ 测试连通性 + 保存 ]
```

### 8.2 启用 / 禁用自己的策略

```python
@app.post("/api/strategies/{instance_id}/enable")
async def enable_strategy(
    instance_id: int,
    user: User = Depends(get_current_user)
):
    instance = await db.strategy_instances.find_one(
        id=instance_id, user_id=user.id    # 强制只能改自己的
    )
    if not instance:
        raise HTTPException(404, "Strategy instance not found")
    await db.strategy_instances.update(id=instance_id, enabled=True)
```

### 8.3 导出自己的数据

```python
@app.get("/api/me/export")
async def export_my_data(user: User = Depends(get_current_user)):
    """导出用户的所有数据(用户主权)"""
    return {
        "user": user.dict(),
        "credentials": [
            # 不包含明文 API key,只包含元数据
            c.dict_safe() for c in await db.credentials.find(user_id=user.id)
        ],
        "strategies": [s.dict() for s in await db.strategy_instances.find(user_id=user.id)],
        "positions": [p.dict() for p in await db.positions.find(user_id=user.id)],
        "orders": [o.dict() for o in await db.orders.find(user_id=user.id)],
        "pnl_history": [...]
    }
```

### 8.4 删除自己的账户

```python
@app.delete("/api/me")
async def delete_my_account(user: User = Depends(get_current_user)):
    """用户主动删除账户"""
    # 平仓
    await close_all_positions(user.id)
    # 删除 DEK(API key 永久不可恢复)
    await db.user_secrets.delete(user_id=user.id)
    # 软删除
    await db.users.update(id=user.id, status="archived")
```

---

## 九、安全加固

### 9.1 必须做的安全措施

#### 1. 密码策略

```python
PASSWORD_REQUIREMENTS = {
    "min_length": 12,                # 最少 12 位
    "require_uppercase": True,
    "require_lowercase": True,
    "require_digits": True,
    "require_special": True,
    "no_common_passwords": True,     # 检查 1万常见密码列表
}
```

#### 2. 强制 2FA

```python
# 添加 API key 之前,必须开 2FA
@app.post("/api/credentials")
async def add_credential(user: User = Depends(get_current_user), ...):
    if not user.totp_enabled:
        raise HTTPException(403, "Enable 2FA before adding API keys")
    ...
```

#### 3. 登录尝试限制

```python
async def login(data: LoginRequest):
    user = await db.users.find_one(username=data.username)
    if not user:
        raise HTTPException(401, "Invalid credentials")

    # 失败次数 > 5 → 锁定 1 小时
    if user.failed_login_count >= 5:
        last_fail = user.last_failed_login_at
        if last_fail and now() - last_fail < timedelta(hours=1):
            raise HTTPException(429, "Account temporarily locked")
        else:
            await db.users.update(id=user.id, failed_login_count=0)

    if not bcrypt.verify(data.password, user.password_hash):
        await db.users.update(
            id=user.id,
            failed_login_count=user.failed_login_count + 1,
            last_failed_login_at=now()
        )
        raise HTTPException(401, "Invalid credentials")

    # 验证 2FA
    if user.totp_enabled and not verify_totp(data.totp_code, user):
        raise HTTPException(401, "Invalid 2FA code")

    # 成功
    await db.users.update(id=user.id, failed_login_count=0, last_login_at=now())
    return create_jwt(user)
```

#### 4. JWT token 安全

```python
# JWT 短时效 + Refresh Token
ACCESS_TOKEN_EXPIRE = timedelta(minutes=15)
REFRESH_TOKEN_EXPIRE = timedelta(days=7)

# 敏感操作(改密码、加 API key)需要二次认证
async def require_recent_auth(token: str):
    payload = decode_jwt(token)
    if now() - payload["iat"] > timedelta(minutes=5):
        raise HTTPException(403, "Re-authenticate required for sensitive operation")
```

#### 5. 审计日志(必须)

```sql
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    actor_user_id BIGINT REFERENCES users(id),    -- 谁做的
    actor_role VARCHAR(20),
    actor_ip INET,

    event_type VARCHAR(50),                       -- credential_decrypted / login / ...
    target_user_id BIGINT REFERENCES users(id),   -- 影响了谁

    details JSONB
);
```

**所有敏感操作都要记**:登录、加 API key、解密 API key、超管查看用户、用户删除账户...

---

## 十、用户隔离的测试要求

实施完成后,**必须做的测试**(必跑通过):

### 10.1 数据隔离测试

```python
# 创建用户 A 和 B
user_a = await create_user("alice")
user_b = await create_user("bob")

# 用户 A 创建持仓
await as_user(user_a).create_position(...)

# 验证用户 B 看不到
positions_b = await as_user(user_b).list_positions()
assert len(positions_b) == 0   # 必须为 0

# 直接 SQL 注入也要拦住
malicious_query = "SELECT * FROM positions"
result = await as_user(user_b).db.execute(malicious_query)
# RLS 应该自动过滤,result 只包含 user_b 的数据
```

### 10.2 API key 隔离测试

```python
# 用户 A 添加 Binance API key
await as_user(user_a).add_credential("binance", "key_aaa", "secret_aaa")

# 用户 B 不能看到
creds_b = await as_user(user_b).list_credentials()
assert len(creds_b) == 0

# 数据库直接查也要确保密文不可解密(因为 DEK 不同)
encrypted = await db.execute("SELECT api_key_encrypted FROM exchange_credentials WHERE user_id = $1", user_a.id)
# 用 user_b 的 DEK 解密 → 必须失败
with pytest.raises(InvalidToken):
    decrypt_with_dek(encrypted, user_b.dek)
```

### 10.3 超管权限边界测试

```python
admin = await create_super_admin()

# 超管不能解密用户 API key
with pytest.raises(HTTPException) as exc:
    await as_user(admin).get_user_credential_plaintext(user_a.id, "binance")
assert exc.value.status_code == 404   # endpoint 不存在

# 超管不能代用户下单
with pytest.raises(HTTPException):
    await as_user(admin).place_order_as(user_a.id, ...)
```

---

## 十一、实施时机和优先级

### 11.1 实施 Phase 划分

**Phase A**(核心系统稳定后,大约第 12-16 周):
- 用户表 + 邀请码
- 主密钥 + DEK 加密架构
- 数据库行级隔离
- 基本注册 / 登录 / 2FA

**Phase B**(Phase A 稳定 1 个月后):
- 超管面板
- 用户级风控
- 用户级通知配置
- 审计日志

**Phase C**(只在你决定商业化时才做):
- 支付集成
- 公开注册 + KYC
- 客服系统
- 计费 / 订阅

### 11.2 准入标准

**只有满足以下条件才能实施 Phase A**:

- ✅ 单租户系统已经实盘稳定运行 **3 个月以上**
- ✅ 你已经经历过至少 1 次"系统出 bug" 的场景,知道怎么处理
- ✅ 你做过完整的数据库备份 + 恢复演练
- ✅ 你愿意承担"朋友亏钱来找你抱怨"的心理压力

如果以上任何一条不满足,**不要急着加多用户支持**——你自己用都还没用稳。

---

## 十二、给朋友的"使用前必读"

每个朋友加入前,**强制让他们读完这个文档**(单独一页):

```markdown
# 使用 Dracula-System 前你必须知道的事

## 这不是专业产品
这是 [你的名字] 个人开发的工具,免费给认识的人用。
没有客服,没有 SLA,没有保险,没有责任承担。

## 加密货币量化交易高风险
本系统跑套利策略,**月化 1%-3% 是合理预期,不是 100%+**。
亏损是正常的,**$5,000 资金可能在 1 个月内亏 $300**。
如果你不能接受这个,请不要使用。

## API key 权限要求
你必须:
- ✅ 关闭 API key 的"提币"权限
- ✅ 设置 IP 白名单(只允许我的服务器 IP)
- ✅ 不要在 API key 上质押任何不能承受损失的金额

## 你的数据
- 你的 API key 加密存储,我也无法直接查看明文
- 你随时可以导出所有数据
- 你随时可以删除账户

## 出问题时
- 联系我(Telegram / 微信)
- 不要在 GitHub Issues / 公开渠道讨论你的损失
- 不要将系统介绍给陌生人

## 如果你不同意上面任何一条
请不要继续使用。我们仍然是朋友 :)
```

---

## 十三、技术目录结构

```
auth/
├── __init__.py
├── encryption.py             -- EncryptionService(三层密钥)
├── credential_service.py     -- CredentialService
├── jwt_service.py            -- JWT 生成 / 验证
├── totp_service.py           -- 2FA / TOTP
├── password_service.py       -- bcrypt + 密码策略
├── invitation_service.py     -- 邀请码
├── audit_log.py              -- 审计日志
└── tests/
    ├── test_encryption.py
    ├── test_isolation.py     -- 用户隔离测试
    └── test_credential.py

api/
├── routes/
│   ├── auth.py               -- 登录 / 注册 / 2FA
│   ├── users.py              -- 自己的账户管理
│   ├── credentials.py        -- API key 管理
│   ├── strategies.py         -- 策略管理(已加 user_id)
│   └── admin/
│       ├── users.py          -- 超管:用户管理
│       ├── invitations.py    -- 超管:邀请码
│       └── audit.py          -- 超管:审计日志查看

scripts/
├── generate_master_key.py
├── rotate_master_key.py
└── backup_master_key.sh
```

---

## 十四、安全自检清单

实施完成后,**自己跑一遍**:

```
=== 加密层 ===
☐ 主密钥不在 git 里(grep -r "master.key" .git → 无结果)
☐ 主密钥不在数据库里
☐ 主密钥文件 chmod 600
☐ 主密钥已经备份到 2 个独立位置(本地 + 云加密)
☐ DEK 用主密钥加密,不裸存数据库
☐ API key 用 DEK 加密,不裸存数据库

=== 隔离层 ===
☐ 所有事务表加了 user_id 字段
☐ RLS 策略已启用,普通用户查询自动过滤
☐ 测试:用户 A 无法查看用户 B 的任何数据
☐ 测试:即使绕过 API 直接查 SQL,RLS 也拦住

=== 认证层 ===
☐ 密码用 bcrypt(不是 MD5/SHA)
☐ 2FA 强制(添加 API key 前必须开)
☐ 登录尝试限制(5 次锁 1 小时)
☐ JWT 短时效(15 分钟 access + 7 天 refresh)
☐ 敏感操作需要 5 分钟内的认证

=== 审计层 ===
☐ 所有敏感操作记录到 audit_log
☐ 审计日志至少保留 1 年
☐ 审计日志不能被普通用户修改 / 删除

=== 业务层 ===
☐ 超管不能查看用户 API key 明文(没有 endpoint)
☐ 超管不能代用户下单(没有 endpoint)
☐ 用户能导出自己所有数据
☐ 用户能完全删除自己账户

=== 法律层 ===
☐ 友情声明 v1.0 已起草
☐ 使用前必读已起草
☐ 不接受任何形式付款(完全免费)
☐ 邀请码只发给认识的人
```

---

## 十五、总结

5 句话:

1. **白名单模式**:你认识的 3-10 个朋友,免费用,有友情声明
2. **三层加密**:主密钥 → DEK → API key,即使数据库泄露也安全
3. **数据库 + API + 应用层三重隔离**,用户互相看不到对方数据
4. **超管(你)能监控,但技术上不能查 API key、不能代用户下单**
5. **未来商业化时,这套架构直接复用**,只需加支付 / KYC / 客服三层

---

## 你的下一步

### 短期(现在 - 12 周)

1. **完成核心系统的 11 章设计 + 代码**
2. **本章先不实施**,先确保自己用能跑稳
3. **每月看一遍这一章**,加深理解

### 中期(12-24 周)

1. **核心系统稳定运行 3 个月**
2. **开始实施 Phase A**(用户表 + 加密 + 隔离)
3. **找 1-2 个最信任的朋友**做 alpha 测试

### 长期(24 周+)

1. **稳定运行 + Phase B(超管面板)**
2. **再过 6 个月,如果一切顺利**,再决定是否进入 Phase C(商业化)
3. **走商业化路线之前,必须先咨询专业律师**——这一章不替代法律咨询

---

**重要提醒**

这一章是**白名单模式**,不是 SaaS。即使技术上做完,**你也不能**:

- ❌ 在公开渠道宣传系统
- ❌ 让陌生人注册
- ❌ 收取任何形式的费用
- ❌ 提供"专业服务"承诺

如果未来你想商业化,**必须先做合规咨询**,然后回头看这一章是否还合用。**当下,你只是在做一个"给朋友用的工具"**——这是法律风险最低、技术上最优雅、未来最有灵活性的路径。
