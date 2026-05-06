"""健康检查脚本

用途:
  - 手动验证基础设施连接
  - CI/CD 部署后验证

用法:
  cd backend
  python scripts/health_check.py

输出:
  JSON 格式，所有检查通过 exit(0)，任意失败 exit(1)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import asyncpg
import redis.asyncio as aioredis

# 加载 .env（如果存在）
_env_file = Path(__file__).parents[2] / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file)


def _db_dsn() -> str:
    return (
        f"postgresql://{os.getenv('POSTGRES_USER', 'dracula')}"
        f":{os.getenv('POSTGRES_PASSWORD', '')}"
        f"@{os.getenv('POSTGRES_HOST', 'localhost')}"
        f":{os.getenv('POSTGRES_PORT', '5432')}"
        f"/{os.getenv('POSTGRES_DB', 'dracula')}"
    )


def _redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


async def check_postgres() -> dict[str, Any]:
    start = time.monotonic()
    try:
        conn = await asyncio.wait_for(asyncpg.connect(_db_dsn()), timeout=5.0)
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        return {
            "ok": True,
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
            "detail": version.split(",")[0] if version else "connected",
        }
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout (5s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def check_timescaledb() -> dict[str, Any]:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(_db_dsn()), timeout=5.0)
        row = await conn.fetchrow(
            "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
        )
        await conn.close()
        if row:
            return {"ok": True, "detail": f"v{row['extversion']}"}
        return {"ok": False, "error": "extension not installed — run: alembic upgrade head"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def check_redis() -> dict[str, Any]:
    start = time.monotonic()
    try:
        client = aioredis.from_url(_redis_url(), socket_connect_timeout=5)
        pong = await asyncio.wait_for(client.ping(), timeout=5.0)
        await client.aclose()
        return {
            "ok": bool(pong),
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
            "detail": "PONG",
        }
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout (5s)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def run_all() -> dict[str, Any]:
    pg, tsdb, rd = await asyncio.gather(
        check_postgres(),
        check_timescaledb(),
        check_redis(),
    )
    checks = {"postgres": pg, "timescaledb": tsdb, "redis": rd}
    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": os.getenv("VERSION", "0.1.0"),
    }


def main() -> None:
    result = asyncio.run(run_all())
    print(json.dumps(result, indent=2))
    if result["status"] != "ok":
        failed = [k for k, v in result["checks"].items() if not v.get("ok")]
        print(f"\n❌ Failed: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)
    print("\n✅ All checks passed", file=sys.stderr)


if __name__ == "__main__":
    main()
