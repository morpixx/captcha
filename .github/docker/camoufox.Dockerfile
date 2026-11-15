# Camoufox-capable image for the `browser` CI job.
#
# The application image (infra/docker/app.Dockerfile) stays on Python 3.14 and
# never installs a browser. This image exists only to give the real-browser
# smoke test a Firefox/Camoufox runtime with the system libraries it needs, so
# the test exercises the same launcher/options path production uses.
#
# Python 3.12 is deliberate: that is the runtime the browser lane ships with
# (the app is 3.14) and the floor declared in pyproject.toml. The stack is
# installed editable so the mounted checkout and the installed package agree.
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Firefox runtime libraries. Kept explicit instead of `playwright install-deps`
# because that also pulls Chromium/WebKit dependencies the lane never uses.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      fonts-liberation \
      libasound2 \
      libatk-bridge2.0-0 \
      libatk1.0-0 \
      libcairo2 \
      libcups2 \
      libdbus-1-3 \
      libdbus-glib-1-2 \
      libdrm2 \
      libgbm1 \
      libgtk-3-0 \
      libnspr4 \
      libnss3 \
      libpango-1.0-0 \
      libx11-xcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxkbcommon0 \
      libxrandr2 \
      libxt6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Only what the install needs; the test suite is mounted at run time so the
# image does not go stale between commits.
COPY pyproject.toml README.md ./
COPY src ./src

# Only the test runner, not the full dev extra: this image exists to run
# tests/integration/browser, not to lint the repository.
RUN pip install -e . pytest pytest-asyncio camoufox \
 && python -m camoufox fetch

WORKDIR /work
