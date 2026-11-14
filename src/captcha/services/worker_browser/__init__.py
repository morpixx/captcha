"""captcha-worker-browser — browser-lane solver worker (playwright/camoufox)."""

from __future__ import annotations

from captcha.services.worker_browser.worker import (
    DEFAULT_CONCURRENCY,
    DEFAULT_TIMEOUT_SEC,
    LANE,
    Worker,
)

__all__ = ["DEFAULT_CONCURRENCY", "DEFAULT_TIMEOUT_SEC", "LANE", "Worker"]
