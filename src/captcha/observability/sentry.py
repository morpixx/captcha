from __future__ import annotations

import logging

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

_log = logging.getLogger(__name__)


def setup_sentry(
    dsn: str,
    environment: str,
    *,
    traces_sample_rate: float = 0.0,
    release: str | None = None,
) -> None:
    """Initialise Sentry. No-op if dsn is empty.

    LoggingIntegration is set to capture warnings+ as breadcrumbs and errors+ as events,
    which matches how structlog surfaces structured errors via stdlib logging.
    """
    if not dsn:
        return
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            traces_sample_rate=traces_sample_rate,
            send_default_pii=False,
            integrations=[
                LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
                AsyncioIntegration(),
            ],
        )
    except Exception:  # noqa: BLE001
        _log.exception("sentry init failed, continuing without")
