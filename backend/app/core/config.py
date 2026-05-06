"""配置加载系统

优先级: 环境变量 > .env 文件 > 默认值
Secrets (.env):          API keys, 数据库密码
运行时参数 (config.yaml): 风控阈值, 杠杆配置, 策略参数
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录 (backend/ 的上级)
_REPO_ROOT = Path(__file__).parents[3]
_CONFIG_DIR = _REPO_ROOT / "config"


# ============================================================
# Secrets & infrastructure (来自 .env)
# ============================================================

class Settings(BaseSettings):
    """所有 secrets 和基础设施配置，从 .env 读取"""

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 系统
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    version: str = Field(default="0.1.0")

    # 数据库
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_user: str = Field(default="dracula")
    postgres_password: str = Field(default="")
    postgres_db: str = Field(default="dracula")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")

    # API 鉴权
    api_secret_key: str = Field(default="")
    api_token: str = Field(default="")

    # 交易所 CEX
    binance_api_key: str = Field(default="")
    binance_api_secret: str = Field(default="")
    bybit_api_key: str = Field(default="")
    bybit_api_secret: str = Field(default="")
    okx_api_key: str = Field(default="")
    okx_api_secret: str = Field(default="")
    okx_api_passphrase: str = Field(default="")

    # 通知
    telegram_bot_token: str = Field(default="")
    telegram_chat_id: str = Field(default="")
    discord_webhook_url: str = Field(default="")
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_from_email: str = Field(default="")
    smtp_to_email: str = Field(default="")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        lower = v.lower()
        if lower not in allowed:
            raise ValueError(f"environment must be one of {allowed}")
        return lower

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Alembic 同步迁移使用"""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


# ============================================================
# Runtime config (来自 config/config.yaml)
# ============================================================

class AppConfig:
    """运行时配置，从 config/config.yaml 读取（非 secrets）"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def risk(self) -> dict[str, Any]:
        return self._data.get("risk", {})

    @property
    def tier3a(self) -> dict[str, Any]:
        """单策略熔断参数"""
        return self.risk.get("tier3a", {})

    @property
    def tier3b(self) -> dict[str, Any]:
        """账户级熔断参数"""
        return self.risk.get("tier3b", {})

    @property
    def tier3c(self) -> dict[str, Any]:
        """强制平仓参数"""
        return self.risk.get("tier3c", {})

    @property
    def leverage_tiers(self) -> dict[str, Any]:
        """分层杠杆配置 (Tier A/B/C)"""
        return self.risk.get("leverage_tiers", {})

    @property
    def exchanges(self) -> dict[str, Any]:
        return self._data.get("exchanges", {})

    @property
    def system(self) -> dict[str, Any]:
        return self._data.get("system", {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回缓存的 Settings 实例（进程内单例）"""
    return Settings()


@functools.lru_cache(maxsize=1)
def get_app_config() -> AppConfig:
    """返回缓存的 AppConfig 实例（进程内单例）"""
    data = _load_yaml(_CONFIG_DIR / "config.yaml")
    return AppConfig(data)
