"""CLI entry — uvicorn.run."""

from __future__ import annotations

import os
import sys

import uvicorn

from captcha.config import get_settings


def main() -> int:
    settings = get_settings()
    host = os.getenv("CAPTCHA_ADMIN_HOST", "0.0.0.0")
    port = int(os.getenv("CAPTCHA_ADMIN_PORT", "8081"))
    # one worker is the sane default — admin is low-traffic, DB-bound, and
    # multiple workers would each open a pg pool
    uvicorn.run(
        "captcha.services.admin.app:app",
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
