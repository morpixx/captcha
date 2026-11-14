# syntax=docker/dockerfile:1.7
# One image for every process. The role is chosen by the compose `command:`,
# the extras by the build arg (cv pulls torch, browser pulls playwright).
FROM python:3.14-slim AS base

ARG EXTRAS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.4.27

WORKDIR /app

# manifests first so the dependency layer caches across source edits
COPY pyproject.toml README.md ./
RUN uv sync --no-dev --no-install-project ${EXTRAS}

COPY src/ ./src/
RUN uv sync --no-dev ${EXTRAS}

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd -r -u 10001 -s /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "captcha.services.gateway"]
