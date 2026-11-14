# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_SYSTEM_PYTHON=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.4.27

WORKDIR /app

# workspace manifests first for better cache reuse
COPY pyproject.toml uv.lock* README.md ./
COPY packages/ ./packages/
COPY solvers/ ./solvers/
COPY services/gateway/ ./services/gateway/

RUN uv sync --frozen --no-dev --package captcha-gateway || \
    uv sync --no-dev --package captcha-gateway

# non-root
RUN useradd -r -u 10001 -s /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

EXPOSE 8080 9090

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "captcha_gateway.main"]
