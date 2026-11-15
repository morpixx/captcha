"""2captcha `callbackUrl` delivery.

One sender per worker process: a single `httpx.AsyncClient` shared by every
worker task, with the timeout and the attempt count coming from Settings.
Delivery is best-effort by contract — a callback the client cannot receive must
never take down a solver — so every failure is logged and reported as a bool.

The callback URL is client-supplied, which makes this an SSRF sink: the gateway
rejects private/metadata literals at create time, and this sender re-checks
before every delivery because a row may predate that check. Only
`scheme://host[:port]` reaches a log line — a callback URL can carry a token in
its path or query. `follow_redirects` stays at httpx's default (off): a public
callback redirecting to `http://10.0.0.1/` is not followed.
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from captcha.adapters.netguard import UrlRejectedError, ensure_public_url_shape

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 10.0
_DEFAULT_ATTEMPTS = 3
_DEFAULT_BACKOFF_SEC = 0.25


def safe_url(url: str) -> str:
    """`scheme://host[:port]` only: no userinfo, path, query or fragment."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable>"
    if not parts.scheme or not parts.hostname:
        return "<unparseable>"
    try:
        port = parts.port
    except ValueError:
        port = None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


class PingbackSender:
    """POSTs a JSON body to a task's callback URL, retrying transient failures."""

    def __init__(
        self,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        max_attempts: int = _DEFAULT_ATTEMPTS,
        backoff_sec: float = _DEFAULT_BACKOFF_SEC,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._attempts = max(1, int(max_attempts))
        self._backoff_sec = max(0.0, float(backoff_sec))
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def send(self, url: str, payload: dict[str, Any]) -> bool:
        """Deliver `payload` to `url`. Returns True on a 2xx, False otherwise."""
        try:
            ensure_public_url_shape(url, field="callbackUrl")
        except UrlRejectedError as exc:
            log.warning(
                "pingback.url_rejected",
                field=exc.context.get("field"),
                host=exc.context.get("host"),
                error=exc.message,
            )
            return False

        destination = safe_url(url)
        for attempt in range(1, self._attempts + 1):
            try:
                response = await self._client.post(url, json=payload)
            except Exception as exc:
                log.warning(
                    "pingback.request_failed",
                    url=destination,
                    attempt=attempt,
                    error=str(exc),
                    error_type=exc.__class__.__name__,
                )
            else:
                if 200 <= response.status_code < 300:
                    log.info("pingback.delivered", url=destination, attempt=attempt, status=response.status_code)
                    return True
                log.warning("pingback.bad_status", url=destination, attempt=attempt, status=response.status_code)
            if attempt < self._attempts and self._backoff_sec:
                await asyncio.sleep(self._backoff_sec * attempt)
        log.error("pingback.failed", url=destination, attempts=self._attempts)
        return False

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = ["PingbackSender", "safe_url"]
