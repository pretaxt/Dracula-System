# Dracula System — Backend

Python 3.11 + FastAPI 后端服务。

## 目录结构

```
backend/
├── app/
│   ├── core/           # 配置加载、日志、依赖注入        ← Week 1
│   ├── exchanges/      # 交易所适配器（CCXT 封装）        ← Week 3
│   ├── strategies/     # 策略实现（#1 #4 #13）           ← Week 5
│   ├── risk/           # 风控引擎（三层熔断）             ← Week 5
│   ├── execution/      # 订单执行引擎                    ← Week 5
│   ├── notifications/  # 通知系统（5 渠道）               ← Week 9
│   ├── api/            # FastAPI 路由
│   │   └── v1/         # API v1 端点
│   └── models/         # Pydantic + SQLAlchemy 模型
├── tests/
│   ├── unit/           # 单元测试（目标 >80%，风控 >95%）
│   └── integration/    # 集成测试（Binance testnet）
├── scripts/            # 运维脚本
└── pyproject.toml
```

## 快速开始

```bash
# 1. 安装依赖（推荐 uv）
pip install uv
uv sync --extra dev

# 2. 复制环境变量
cp ../.env.example ../.env.development

# 3. 启动基础服务
docker compose -f ../docker-compose.yml up -d

# 4. 运行测试
pytest
```

## 编码规范

- 金额 / 价格：必须用 `Decimal`，禁止 `float`
- 日志：必须用 `structlog`，禁止 `print`
- 异常：必须捕获具体类型，禁止 `except Exception`
- 并发：必须用 `asyncio.gather`，禁止在循环内 `await`
- Secrets：必须走 `.env`，禁止硬编码

详见根目录 `.claude_code_init.md`。
