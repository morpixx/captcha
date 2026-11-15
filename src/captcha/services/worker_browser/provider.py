"""Browser provider resolution for the browser lane (contract §4).

`ctx.browser` is `adapters.browser.provider.CamoufoxProvider`: one fresh
Camoufox launch and one temporary profile per task, killed and reaped by
browser-core's `Supervisor`. The worker never launches a browser itself and
never shares a context: the composition root (`cli`) resolves one provider per
process and hands it to every consumer.
"""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


def resolve_provider(settings: Any, *, headless: bool = True, provider: Any = None) -> Any:
    """Return the provider this process should use. Does not launch anything."""
    if provider is not None:
        return provider
    # Local import: keeps camoufox/psutil out of the import graph until a worker
    # actually starts, which matters for the mock-only test suite.
    from captcha.adapters.browser.provider import CamoufoxProvider

    provider = CamoufoxProvider(headless=headless)
    log.info(
        "browser.provider.resolved",
        provider=type(provider).__name__,
        sessions=getattr(settings, "worker_browser_sessions", None),
    )
    return provider


async def close_provider(provider: Any) -> None:
    """Close a provider by whichever shutdown hook it exposes."""
    for name in ("aclose", "close", "shutdown", "close_all"):
        fn = getattr(provider, name, None)
        if callable(fn):
            await fn()
            return
