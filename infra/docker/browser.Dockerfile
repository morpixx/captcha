# syntax=docker/dockerfile:1.7
# Browser lane image: Python + Camoufox/Firefox + its system libraries, Node 20
# for the hCaptcha bundle worker, and one non-root user for both.
#
# It is ~2.5 GB because of the Firefox build, so it is built separately and not
# part of `make dev`:
#
#   docker compose --profile browser build worker-browser
#
# PYTHON_VERSION stays at the Debian bookworm / 3.12 pair the working browser
# stack was validated on: the lib package names below are bookworm's, and 3.12 is
# the floor pyproject declares. The app image (app.Dockerfile) is on 3.14-slim; the
# two images never share a venv, so they are allowed to differ. Bump both
# together after a browser-lane canary on the new interpreter.
ARG PYTHON_VERSION=3.12
ARG CAMOUFOX_VERSION=0.5.6

# --- hCaptcha bundle worker dependencies -------------------------------------
# Keep in sync with infra/docker/app.Dockerfile.
FROM node:20-bookworm-slim AS node-bundle

WORKDIR /build

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential pkg-config python3 \
      libcairo2-dev libpango1.0-dev libjpeg-dev libgif-dev librsvg2-dev \
 && rm -rf /var/lib/apt/lists/*

COPY src/captcha/adapters/hcaptcha_js/package.json src/captcha/adapters/hcaptcha_js/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# --- Camoufox/Firefox, fetched in its own layer ------------------------------
# A source edit must not re-download ~2.5 GB of browser. Everything camoufox
# installs is keyed off XDG_CACHE_HOME, so the cache is one directory to copy.
FROM python:${PYTHON_VERSION}-slim-bookworm AS camoufox

ARG CAMOUFOX_VERSION
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/opt/camoufox-cache

RUN pip install --no-cache-dir "camoufox[geoip]==${CAMOUFOX_VERSION}"
RUN python -m camoufox fetch \
 && (python -c "from camoufox.geolocation import download_mmdb; download_mmdb()" \
     || echo "WARN: GeoIP prefetch failed; the mmdb is downloaded on first proxied task")

# --- runtime -----------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS browser

ARG EXTRAS=""
ARG CAMOUFOX_VERSION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # Camoufox locates the browser through platformdirs, which honours this.
    XDG_CACHE_HOME=/opt/camoufox-cache \
    # Firefox probes ~/.camoufox even for a temporary profile; camoufox refuses
    # to launch if HOME is not writable, so the image gives it one.
    HOME=/home/app \
    TMPDIR=/tmp \
    NODE_ENV=production

# Firefox/Camoufox system libraries (the known-good production set, plus the
# GTK/pixbuf/X11 pieces Playwright's firefox deps add) and the font stack
# Camoufox expects.
# A missing font here changes the canvas fingerprint, so these are not optional.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tini libstdc++6 \
      # headful debugging (WORKER_BROWSER_HEADLESS=0) needs a virtual display;
      # production runs headless=True and never starts it.
      xvfb \
      fonts-liberation fonts-liberation2 fonts-noto-color-emoji fonts-dejavu-core \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 \
      libcairo-gobject2 libcairo2 libcups2 \
      libdbus-1-3 libdbus-glib-1-2 libdrm2 libegl1 libexpat1 \
      libfontconfig1 libfreetype6 libgbm1 libgdk-pixbuf-2.0-0 libgl1 libglib2.0-0 \
      libgtk-3-0 libnspr4 libnss3 \
      libpango-1.0-0 libpci3 libudev1 \
      libx11-6 libx11-xcb1 libxcb1 libxcb-dri3-0 libxcb-glx0 libxcb-icccm4 \
      libxcb-image0 libxcb-keysyms1 libxcb-present0 libxcb-randr0 libxcb-render0 \
      libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-util1 libxcb-xfixes0 \
      libxcb-xinerama0 libxcb-xkb1 libxcomposite1 libxcursor1 libxdamage1 libxext6 \
      libxfixes3 libxi6 libxkbcommon-x11-0 libxkbcommon0 libxrandr2 libxrender1 \
      libxt6 libxtst6 \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.4.27

WORKDIR /app

# manifests first so the dependency layer caches across source edits
COPY pyproject.toml README.md ./
RUN uv sync --no-dev --no-install-project ${EXTRAS}

COPY src/ ./src/
RUN uv sync --no-dev ${EXTRAS}

# camoufox is not a pyproject extra (it is ~2.5 GB with its browser and belongs
# in one image only), so it is installed into the project venv here. This must
# stay *after* the last `uv sync`, which prunes what the project does not list.
RUN uv pip install --python /app/.venv "camoufox[geoip]==${CAMOUFOX_VERSION}"

COPY --from=camoufox /opt/camoufox-cache /opt/camoufox-cache
COPY --from=node-bundle /usr/local/bin/node /usr/local/bin/node
COPY --from=node-bundle /build/node_modules ./src/captcha/adapters/hcaptcha_js/node_modules

COPY infra/docker/healthcheck.py /usr/local/bin/captcha-healthcheck
RUN chmod +x /usr/local/bin/captcha-healthcheck

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app \
 && mkdir -p /home/app/.camoufox \
 && chmod 700 /home/app/.camoufox \
 && chown -R app:app /app /opt/camoufox-cache /home/app
USER app

# No HTTP port: the worker's liveness is Redis plus an installed browser. A
# missing/failed `camoufox fetch` in the image must fail the container, not the
# first hCaptcha task twelve hours later.
HEALTHCHECK --interval=30s --timeout=15s --start-period=90s --retries=3 \
  CMD ["python", "/usr/local/bin/captcha-healthcheck", "--redis", "--browser"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "captcha.services.worker_browser"]
