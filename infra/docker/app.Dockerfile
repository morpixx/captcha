# syntax=docker/dockerfile:1.7
# One image for every process. The role is chosen by the compose `command:`,
# the extras by the build arg (cv pulls torch).
#
# The image carries Node 20 and the hCaptcha bundle worker's node_modules for
# every role, not only for worker-http: one image for all processes is the
# design, and worker-http is the role that spawns that Node process. Node +
# jsdom + canvas is ~180 MB. The browser image repeats the same stage because a
# Dockerfile cannot inherit from another one's stage.

# --- hCaptcha bundle worker dependencies -------------------------------------
# canvas ships linux-x64 prebuilts for node 20; the toolchain below is only the
# fallback for the build-from-source path.
FROM node:20-bookworm-slim AS node-bundle

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential pkg-config python3 \
      libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev \
 && rm -rf /var/lib/apt/lists/*

# package-lock.json is committed: `npm ci` needs it, and a floating install
# inside an image build is not a dependency policy.
COPY src/captcha/adapters/hcaptcha_js/package.json src/captcha/adapters/hcaptcha_js/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# --- application -------------------------------------------------------------
FROM python:3.14-slim AS base

ARG EXTRAS=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini libstdc++6 \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.4.27

WORKDIR /app

# manifests first so the dependency layer caches across source edits
COPY pyproject.toml README.md ./
RUN uv sync --no-dev --no-install-project ${EXTRAS}

COPY src/ ./src/

# Node runtime + the worker's node_modules. Both live next to server.js so
# `node server.js` resolves them without NODE_PATH; the bundle worker writes
# its 12h cache into <that dir>/.cache, which app owns below.
COPY --from=node-bundle /usr/local/bin/node /usr/local/bin/node
COPY --from=node-bundle /build/node_modules ./src/captcha/adapters/hcaptcha_js/node_modules

RUN uv sync --no-dev ${EXTRAS}

# Queue workers have no HTTP port, so their healthcheck asks Redis instead.
COPY infra/docker/healthcheck.py /usr/local/bin/captcha-healthcheck
RUN chmod +x /usr/local/bin/captcha-healthcheck

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd -r -u 10001 -s /usr/sbin/nologin app \
 && chown -R app:app /app
USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "captcha.services.gateway"]
