# 08 · 数据库 Schema 设计 · 完整设计

> **范围**:PostgreSQL + TimescaleDB 的所有表结构
> **目标**:为所有策略和模块提供持久化层
> **预期数据量**:
> - 时序数据(价格、资金费率):每天 50-200 GB
> - 事务数据(持仓、订单):每月 < 1 GB

---

## 一、设计原则

### 1.1 两类数据,两种引擎

**事务型数据**(PostgreSQL):
- 订单、持仓、配置、用户设置
- ACID 保证(必须不能出错)
- 频繁更新,量小但重要

**时序型数据**(TimescaleDB):
- 价格 K 线、资金费率历史、PnL 时序
- 高频写入(每秒几百-几千条)
- 主要做范围查询(过去 N 天的数据)
- 量大但容忍少量丢失

### 1.2 命名规范

```
表名:    snake_case 复数 (positions, opportunities)
字段名:  snake_case 单数 (created_at, exchange_name)
索引:    idx_<table>_<columns>
外键:    fk_<table>_<referenced_table>
```

### 1.3 通用字段

每张事务表都有:

```sql
id BIGSERIAL PRIMARY KEY,                         -- 自增主键
uuid UUID DEFAULT gen_random_uuid() NOT NULL,     -- 业务 ID
created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
```

---

## 二、事务型表(PostgreSQL)

### 2.1 strategy_instances 策略实例表

```sql
CREATE TABLE strategy_instances (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL UNIQUE,

    instance_name VARCHAR(100) NOT NULL UNIQUE,
    strategy_type VARCHAR(50) NOT NULL,

    enabled BOOLEAN DEFAULT FALSE NOT NULL,
    config_yaml TEXT NOT NULL,
    config_version INT DEFAULT 1,

    -- 资金
    allocated_capital DECIMAL(20, 8) NOT NULL,
    available_capital DECIMAL(20, 8) NOT NULL,

    -- 累计统计
    total_pnl DECIMAL(20, 8) DEFAULT 0,
    total_fees_paid DECIMAL(20, 8) DEFAULT 0,
    total_funding_received DECIMAL(20, 8) DEFAULT 0,
    total_positions_opened INT DEFAULT 0,
    total_positions_closed INT DEFAULT 0,

    last_active_at TIMESTAMPTZ,
    halt_reason VARCHAR(255),

    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_strategy_instances_enabled ON strategy_instances(enabled);
CREATE INDEX idx_strategy_instances_type ON strategy_instances(strategy_type);
```

### 2.2 opportunities 机会扫描记录

```sql
CREATE TABLE opportunities (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL,

    strategy_instance VARCHAR(100) NOT NULL,
    strategy_type VARCHAR(50) NOT NULL,

    exchange VARCHAR(30) NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    instrument_type VARCHAR(20) NOT NULL,

    -- 不同策略类型用不同字段
    apr_pct DECIMAL(10, 4),                    -- 资金费率套利
    funding_rate DECIMAL(10, 8),
    basis_pct DECIMAL(10, 4),                  -- 基差套利
    premium_pct DECIMAL(10, 4),                -- 期现套利
    iv_rv_ratio DECIMAL(10, 4),                -- 期权
    cex_dex_spread_pct DECIMAL(10, 4),         -- CEX-DEX

    spot_orderbook_depth_usd DECIMAL(20, 2),
    perp_orderbook_depth_usd DECIMAL(20, 2),

    status VARCHAR(20) DEFAULT 'detected',
    -- detected/passed/rejected/expired/used
    rejection_reason VARCHAR(255),

    detected_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    expires_at TIMESTAMPTZ,
    used_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_opportunities_strategy ON opportunities(strategy_instance, detected_at DESC);
CREATE INDEX idx_opportunities_symbol ON opportunities(exchange, symbol);
CREATE INDEX idx_opportunities_status ON opportunities(status);
```

### 2.3 positions 持仓表

```sql
CREATE TABLE positions (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL,

    strategy_instance VARCHAR(100) NOT NULL,
    strategy_type VARCHAR(50) NOT NULL,
    opportunity_id BIGINT REFERENCES opportunities(id),

    status VARCHAR(20) NOT NULL,
    -- pending/open/closing/closed/failed

    notional_usd DECIMAL(20, 2) NOT NULL,
    margin_used DECIMAL(20, 8) NOT NULL,

    target_delta DECIMAL(10, 6) DEFAULT 0,
    current_delta DECIMAL(10, 6),
    delta_drift_pct DECIMAL(10, 4),

    target_apr_pct DECIMAL(10, 4),
    realized_pnl DECIMAL(20, 8) DEFAULT 0,
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0,
    funding_received DECIMAL(20, 8) DEFAULT 0,
    fees_paid DECIMAL(20, 8) DEFAULT 0,
    slippage_loss DECIMAL(20, 8) DEFAULT 0,

    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    holding_hours DECIMAL(10, 2),

    exit_reason VARCHAR(50),
    exit_pnl_pct DECIMAL(10, 4),

    notes TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_positions_strategy ON positions(strategy_instance, status);
CREATE INDEX idx_positions_status ON positions(status);
CREATE INDEX idx_positions_opened_at ON positions(opened_at DESC);
```

### 2.4 position_legs 持仓的腿

```sql
CREATE TABLE position_legs (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL,

    position_id BIGINT NOT NULL REFERENCES positions(id) ON DELETE CASCADE,

    exchange VARCHAR(30) NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    instrument_type VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,                  -- long/short

    size DECIMAL(20, 8) NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    current_price DECIMAL(20, 8),
    leverage DECIMAL(10, 2) DEFAULT 1,
    margin DECIMAL(20, 8) NOT NULL,

    realized_pnl DECIMAL(20, 8) DEFAULT 0,
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0,
    funding_paid DECIMAL(20, 8) DEFAULT 0,
    fees_paid DECIMAL(20, 8) DEFAULT 0,

    status VARCHAR(20) NOT NULL,

    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_position_legs_position ON position_legs(position_id);
CREATE INDEX idx_position_legs_exchange_symbol ON position_legs(exchange, symbol);
CREATE INDEX idx_position_legs_status ON position_legs(status);
```

### 2.5 orders 订单表

```sql
CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL,

    leg_id BIGINT REFERENCES position_legs(id),
    position_id BIGINT REFERENCES positions(id),

    exchange VARCHAR(30) NOT NULL,
    exchange_order_id VARCHAR(100),

    symbol VARCHAR(30) NOT NULL,
    side VARCHAR(10) NOT NULL,
    order_type VARCHAR(20) NOT NULL,
    time_in_force VARCHAR(10),
    post_only BOOLEAN DEFAULT FALSE,
    reduce_only BOOLEAN DEFAULT FALSE,

    requested_size DECIMAL(20, 8) NOT NULL,
    filled_size DECIMAL(20, 8) DEFAULT 0,
    requested_price DECIMAL(20, 8),
    avg_fill_price DECIMAL(20, 8),

    status VARCHAR(20) NOT NULL,
    -- pending/open/partial/filled/canceled/rejected/expired

    error_code VARCHAR(50),
    error_message TEXT,

    submitted_at TIMESTAMPTZ DEFAULT NOW(),
    filled_at TIMESTAMPTZ,
    canceled_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_orders_leg ON orders(leg_id);
CREATE INDEX idx_orders_position ON orders(position_id);
CREATE INDEX idx_orders_exchange_status ON orders(exchange, status);
CREATE INDEX idx_orders_submitted_at ON orders(submitted_at DESC);
```

### 2.6 risk_events 风控事件

```sql
CREATE TABLE risk_events (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL,

    severity VARCHAR(20) NOT NULL,           -- info/warn/critical
    tier INT NOT NULL,                       -- 1/2/3
    event_type VARCHAR(50) NOT NULL,

    strategy_instance VARCHAR(100),
    position_id BIGINT REFERENCES positions(id),

    metric_name VARCHAR(50),
    metric_value DECIMAL(20, 8),
    threshold DECIMAL(20, 8),

    description TEXT NOT NULL,
    action_taken TEXT,

    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by VARCHAR(100),
    acknowledged_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_risk_events_severity ON risk_events(severity, created_at DESC);
CREATE INDEX idx_risk_events_strategy ON risk_events(strategy_instance);
CREATE INDEX idx_risk_events_type ON risk_events(event_type);
```

### 2.7 notifications 通知记录

```sql
CREATE TABLE notifications (
    id BIGSERIAL PRIMARY KEY,
    uuid UUID DEFAULT gen_random_uuid() NOT NULL,

    level VARCHAR(20) NOT NULL,              -- info/success/warn/critical
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,

    strategy_instance VARCHAR(100),
    position_id BIGINT REFERENCES positions(id),
    risk_event_id BIGINT REFERENCES risk_events(id),

    channels TEXT[] NOT NULL,
    delivered_channels TEXT[],
    delivery_attempts INT DEFAULT 0,

    read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMPTZ,
    actioned BOOLEAN DEFAULT FALSE,

    metadata JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

CREATE INDEX idx_notifications_level ON notifications(level, created_at DESC);
CREATE INDEX idx_notifications_read ON notifications(read, created_at DESC);
CREATE INDEX idx_notifications_strategy ON notifications(strategy_instance);
```

### 2.8 system_state 系统状态(单行表)

```sql
CREATE TABLE system_state (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),

    state VARCHAR(20) NOT NULL DEFAULT 'running',
    -- running/paused/halted/maintenance

    halted_at TIMESTAMPTZ,
    halt_reason TEXT,

    last_heartbeat TIMESTAMPTZ DEFAULT NOW(),
    version VARCHAR(20),

    total_capital DECIMAL(20, 8),
    total_pnl_today DECIMAL(20, 8) DEFAULT 0,
    total_pnl_all_time DECIMAL(20, 8) DEFAULT 0,
    daily_drawdown_pct DECIMAL(10, 4) DEFAULT 0,
    weekly_drawdown_pct DECIMAL(10, 4) DEFAULT 0,

    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

INSERT INTO system_state (id, state) VALUES (1, 'running')
ON CONFLICT (id) DO NOTHING;
```

### 2.9 user_settings 用户配置

```sql
CREATE TABLE user_settings (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),

    notification_matrix JSONB NOT NULL DEFAULT '{
        "info":     {"telegram": false, "discord": false, "email": false, "toast": true,  "system": false},
        "success":  {"telegram": true,  "discord": false, "email": false, "toast": true,  "system": false},
        "warn":     {"telegram": true,  "discord": true,  "email": false, "toast": true,  "system": true},
        "critical": {"telegram": true,  "discord": true,  "email": true,  "toast": true,  "system": true}
    }'::jsonb,

    scan_min_apr DECIMAL(10, 4) DEFAULT 10.0,
    default_position_size_usd DECIMAL(20, 2) DEFAULT 500,
    max_concurrent_positions INT DEFAULT 5,

    telegram_chat_id VARCHAR(50),
    discord_webhook_url_encrypted TEXT,
    email_to VARCHAR(100),

    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

INSERT INTO user_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
```

---

## 三、时序型表(TimescaleDB)

### 3.1 安装

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
```

### 3.2 funding_rate_history 资金费率历史

```sql
CREATE TABLE funding_rate_history (
    time TIMESTAMPTZ NOT NULL,
    exchange VARCHAR(30) NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    instrument_type VARCHAR(20) NOT NULL,

    funding_rate DECIMAL(15, 10) NOT NULL,
    apr_pct DECIMAL(10, 4),
    next_funding_time TIMESTAMPTZ,
    funding_interval_hours INT,

    mark_price DECIMAL(20, 8),
    index_price DECIMAL(20, 8)
);

SELECT create_hypertable('funding_rate_history', 'time',
    chunk_time_interval => INTERVAL '7 days');

CREATE INDEX idx_funding_history_lookup
    ON funding_rate_history(exchange, symbol, time DESC);

ALTER TABLE funding_rate_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'exchange,symbol'
);

SELECT add_compression_policy('funding_rate_history', INTERVAL '7 days');
SELECT add_retention_policy('funding_rate_history', INTERVAL '730 days');
```

### 3.3 price_history 价格历史(K 线)

```sql
CREATE TABLE price_history (
    time TIMESTAMPTZ NOT NULL,
    exchange VARCHAR(30) NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    instrument_type VARCHAR(20) NOT NULL,
    interval VARCHAR(10) NOT NULL,           -- 1m/5m/1h/1d

    open DECIMAL(20, 8) NOT NULL,
    high DECIMAL(20, 8) NOT NULL,
    low DECIMAL(20, 8) NOT NULL,
    close DECIMAL(20, 8) NOT NULL,
    volume DECIMAL(20, 8),
    quote_volume DECIMAL(20, 8),
    trades_count INT
);

SELECT create_hypertable('price_history', 'time',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_price_history_lookup
    ON price_history(exchange, symbol, interval, time DESC);

ALTER TABLE price_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'exchange,symbol,interval'
);

SELECT add_compression_policy('price_history', INTERVAL '7 days');
SELECT add_retention_policy('price_history', INTERVAL '730 days');
```

### 3.4 pnl_timeseries 实时 PnL 时序

```sql
CREATE TABLE pnl_timeseries (
    time TIMESTAMPTZ NOT NULL,
    strategy_instance VARCHAR(100) NOT NULL,
    position_id BIGINT,

    -- PnL 归因
    price_pnl DECIMAL(20, 8) DEFAULT 0,
    funding_pnl DECIMAL(20, 8) DEFAULT 0,
    basis_pnl DECIMAL(20, 8) DEFAULT 0,
    theta_pnl DECIMAL(20, 8) DEFAULT 0,
    vega_pnl DECIMAL(20, 8) DEFAULT 0,
    fees_paid DECIMAL(20, 8) DEFAULT 0,
    slippage_loss DECIMAL(20, 8) DEFAULT 0,
    net_pnl DECIMAL(20, 8) NOT NULL,

    -- 累计
    cumulative_pnl DECIMAL(20, 8),

    -- 账户余额
    total_capital DECIMAL(20, 8),
    available_capital DECIMAL(20, 8)
);

SELECT create_hypertable('pnl_timeseries', 'time',
    chunk_time_interval => INTERVAL '1 day');

CREATE INDEX idx_pnl_timeseries_strategy
    ON pnl_timeseries(strategy_instance, time DESC);

ALTER TABLE pnl_timeseries SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'strategy_instance'
);

SELECT add_compression_policy('pnl_timeseries', INTERVAL '7 days');
SELECT add_retention_policy('pnl_timeseries', INTERVAL '1825 days');  -- 5 年
```

### 3.5 orderbook_snapshots 订单簿快照(可选)

```sql
-- 仅在做高频策略或回测时需要
CREATE TABLE orderbook_snapshots (
    time TIMESTAMPTZ NOT NULL,
    exchange VARCHAR(30) NOT NULL,
    symbol VARCHAR(30) NOT NULL,
    instrument_type VARCHAR(20) NOT NULL,

    bids JSONB NOT NULL,    -- [[price, size], ...]
    asks JSONB NOT NULL,

    spread DECIMAL(15, 8),
    mid_price DECIMAL(20, 8),
    depth_5_bid_usd DECIMAL(20, 2),
    depth_5_ask_usd DECIMAL(20, 2)
);

SELECT create_hypertable('orderbook_snapshots', 'time',
    chunk_time_interval => INTERVAL '1 day');

-- 这个表数据量极大,只保留 30 天
SELECT add_retention_policy('orderbook_snapshots', INTERVAL '30 days');
```

### 3.6 iv_rv_history 期权 IV/RV 历史

```sql
CREATE TABLE iv_rv_history (
    time TIMESTAMPTZ NOT NULL,
    underlying VARCHAR(20) NOT NULL,         -- BTC/ETH
    dte INT NOT NULL,                        -- days to expiry

    iv_atm DECIMAL(10, 4),                   -- ATM 隐含波动率
    iv_25d_call DECIMAL(10, 4),              -- 25-delta call IV
    iv_25d_put DECIMAL(10, 4),

    rv_30d DECIMAL(10, 4),
    rv_7d DECIMAL(10, 4),

    iv_rv_ratio DECIMAL(10, 4)
);

SELECT create_hypertable('iv_rv_history', 'time',
    chunk_time_interval => INTERVAL '7 days');

CREATE INDEX idx_iv_rv_history_lookup
    ON iv_rv_history(underlying, dte, time DESC);

SELECT add_retention_policy('iv_rv_history', INTERVAL '730 days');
```

---

## 四、视图(简化常用查询)

### 4.1 当前持仓汇总视图

```sql
CREATE VIEW v_current_positions AS
SELECT
    p.id, p.uuid,
    p.strategy_instance, p.strategy_type,
    p.notional_usd, p.margin_used,
    p.realized_pnl + p.unrealized_pnl AS total_pnl,
    p.target_apr_pct,
    p.opened_at,
    EXTRACT(EPOCH FROM (NOW() - p.opened_at))/3600 AS hours_open,
    array_agg(
        jsonb_build_object(
            'exchange', l.exchange,
            'symbol', l.symbol,
            'side', l.side,
            'size', l.size,
            'entry_price', l.entry_price
        )
    ) AS legs
FROM positions p
LEFT JOIN position_legs l ON l.position_id = p.id
WHERE p.status = 'open'
GROUP BY p.id;
```

### 4.2 每日 PnL 汇总

```sql
CREATE MATERIALIZED VIEW v_daily_pnl_summary AS
SELECT
    DATE(time) AS day,
    strategy_instance,
    SUM(net_pnl) AS daily_net_pnl,
    SUM(funding_pnl) AS daily_funding,
    SUM(fees_paid) AS daily_fees,
    SUM(slippage_loss) AS daily_slippage,
    MAX(cumulative_pnl) AS day_end_cumulative_pnl
FROM pnl_timeseries
GROUP BY DATE(time), strategy_instance;

CREATE INDEX idx_daily_pnl_day ON v_daily_pnl_summary(day DESC);

-- 每天午夜刷新
SELECT add_continuous_aggregate_policy('v_daily_pnl_summary',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');
```

### 4.3 风控事件未确认列表

```sql
CREATE VIEW v_unacknowledged_risk_events AS
SELECT *
FROM risk_events
WHERE acknowledged = FALSE
  AND severity IN ('warn', 'critical')
ORDER BY severity DESC, created_at DESC;
```

---

## 五、触发器

### 5.1 自动更新 updated_at

```sql
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 应用到所有事务表
CREATE TRIGGER set_updated_at
BEFORE UPDATE ON strategy_instances
FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at
BEFORE UPDATE ON positions
FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at
BEFORE UPDATE ON position_legs
FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at
BEFORE UPDATE ON orders
FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
```

### 5.2 持仓状态变更日志

```sql
CREATE TABLE position_status_log (
    id BIGSERIAL PRIMARY KEY,
    position_id BIGINT NOT NULL REFERENCES positions(id),
    old_status VARCHAR(20),
    new_status VARCHAR(20) NOT NULL,
    changed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION log_position_status_change()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status != NEW.status THEN
        INSERT INTO position_status_log (position_id, old_status, new_status)
        VALUES (NEW.id, OLD.status, NEW.status);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER log_position_status
AFTER UPDATE OF status ON positions
FOR EACH ROW EXECUTE FUNCTION log_position_status_change();
```

---

## 六、初始化脚本

```python
# scripts/init_db.py

import asyncio
import asyncpg
from pathlib import Path

async def init_database():
    conn = await asyncpg.connect(...)

    # 1. 安装扩展
    await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
    await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")  # for gen_random_uuid

    # 2. 执行 schema
    schema_sql = Path("scripts/schema.sql").read_text()
    await conn.execute(schema_sql)

    # 3. 创建索引
    indexes_sql = Path("scripts/indexes.sql").read_text()
    await conn.execute(indexes_sql)

    # 4. 创建触发器
    triggers_sql = Path("scripts/triggers.sql").read_text()
    await conn.execute(triggers_sql)

    # 5. 创建视图
    views_sql = Path("scripts/views.sql").read_text()
    await conn.execute(views_sql)

    # 6. 插入初始数据
    await conn.execute("""
        INSERT INTO system_state (id, state, version)
        VALUES (1, 'running', '0.1.0')
        ON CONFLICT (id) DO NOTHING;
    """)
    await conn.execute("""
        INSERT INTO user_settings (id) VALUES (1)
        ON CONFLICT (id) DO NOTHING;
    """)

    await conn.close()
    print("✅ Database initialized")

if __name__ == "__main__":
    asyncio.run(init_database())
```

---

## 七、性能优化

### 7.1 关键索引

每张表已包含必要索引,但**在线上跑一段时间后**应该:

```sql
-- 找到慢查询
SELECT query, calls, mean_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;

-- 检查没用到的索引
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan = 0;
```

### 7.2 时序表压缩比

TimescaleDB 压缩后体积通常压缩到 **5%-15%**:
- 1 天的资金费率数据未压缩约 50MB,压缩后 5MB
- 1 年的数据未压缩 18GB,压缩后 1.8GB

### 7.3 分区策略

```sql
-- 检查 hypertable 分片情况
SELECT hypertable_name, chunk_name, range_start, range_end
FROM timescaledb_information.chunks
WHERE hypertable_name = 'funding_rate_history'
ORDER BY range_end DESC;
```

---

## 八、备份与灾难恢复

### 8.1 备份策略

```bash
# 每天凌晨 4 点全量备份
pg_dump -h localhost -U dracula -d dracula -F c -f /backup/dracula_$(date +%Y%m%d).dump

# 保留 30 天
find /backup -name "dracula_*.dump" -mtime +30 -delete
```

### 8.2 关键数据导出

```sql
-- 紧急情况下,只导出"事务型"数据(几 MB)
COPY strategy_instances TO '/backup/strategy_instances.csv' CSV HEADER;
COPY positions TO '/backup/positions.csv' CSV HEADER;
COPY position_legs TO '/backup/position_legs.csv' CSV HEADER;
COPY orders TO '/backup/orders.csv' CSV HEADER;
```

时序数据丢失影响小(可以从交易所重新拉),所以备份重点是事务表。

---

## 九、目录结构

```
data/
├── __init__.py
├── connection.py           -- 数据库连接池
├── schema.sql              -- 完整 schema(本文档的所有 SQL)
├── indexes.sql             -- 索引定义
├── triggers.sql            -- 触发器
├── views.sql               -- 视图
├── repositories/
│   ├── __init__.py
│   ├── strategy_repo.py
│   ├── position_repo.py
│   ├── order_repo.py
│   ├── opportunity_repo.py
│   ├── risk_event_repo.py
│   └── notification_repo.py
└── timescale/
    ├── __init__.py
    ├── funding_rate_repo.py
    ├── price_history_repo.py
    └── pnl_repo.py
```

---

## 十、总结

5 句话:

1. **PostgreSQL 存事务,TimescaleDB 存时序**(同一个数据库实例)
2. **9 张事务表 + 5 张时序表** 覆盖所有需求
3. **关键设计**:positions ↔ position_legs ↔ orders 三层结构
4. **保留策略**:时序数据 2 年,期权 IV 数据 2 年,订单簿 30 天
5. **自动化**:触发器自动维护 updated_at 和状态日志

---

## 你的下一步

1. 接下来第 9 篇:通知系统设计
2. 之后 10、11 篇,然后合并 PDF
