FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
RUN pip install uv --no-cache-dir

# Copy dependency files
COPY pyproject.toml ./

# Install production dependencies
RUN uv pip install --system --no-cache \
    fastapi \
    "uvicorn[standard]" \
    pydantic-settings \
    sqlalchemy \
    asyncpg \
    alembic \
    redis \
    structlog \
    pyyaml \
    ccxt \
    aiohttp \
    websockets \
    "python-jose[cryptography]" \
    httpx \
    web3

# Copy application code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Create logs directory
RUN mkdir -p /app/logs

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
