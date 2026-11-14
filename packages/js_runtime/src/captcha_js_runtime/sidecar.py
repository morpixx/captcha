"""Node sidecar client. One HTTP round-trip per script; the sidecar handles isolation,
timeout, and memory ceiling. Used when py-mini-racer can't run the challenge (missing
WebCrypto surface, bleeding-edge ES features, or scripts over its memory budget).
"""
from __future__ import annotations

from typing import Any

import httpx

from captcha_common.errors import JSRuntimeError


class SidecarClient:
    """Async client against `sidecar-node/server.js`.

    One `SidecarClient` per worker is fine — httpx.AsyncClient connection-pools. Call
    `close()` on shutdown so idle keepalive connections drain.
    """

    def __init__(
        self,
        url: str,
        *,
        default_timeout_ms: int = 5000,
        pool_limits: httpx.Limits | None = None,
    ) -> None:
        self._base = url.rstrip("/")
        self._default_timeout_ms = default_timeout_ms
        limits = pool_limits or httpx.Limits(max_connections=64, max_keepalive_connections=32)
        # http timeout is slightly longer than js timeout so the sidecar's own timeout
        # error surfaces instead of our client-side cancel.
        self._client = httpx.AsyncClient(
            base_url=self._base,
            limits=limits,
            timeout=httpx.Timeout(connect=2.0, read=60.0, write=10.0, pool=5.0),
        )

    async def exec_script(
        self,
        code: str,
        context_globals: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> Any:
        payload = {
            "script": code,
            "globals": context_globals or {},
            "timeout": timeout_ms if timeout_ms is not None else self._default_timeout_ms,
        }
        try:
            resp = await self._client.post("/exec", json=payload)
        except httpx.TimeoutException as e:
            raise JSRuntimeError(
                "sidecar request timed out",
                context={"timeout_ms": payload["timeout"]},
            ) from e
        except httpx.HTTPError as e:
            raise JSRuntimeError(
                "sidecar transport error",
                context={"error": str(e)},
            ) from e

        if resp.status_code >= 500:
            raise JSRuntimeError(
                "sidecar server error",
                context={"status": resp.status_code, "body": resp.text[:500]},
            )

        try:
            body = resp.json()
        except ValueError as e:
            raise JSRuntimeError(
                "sidecar returned non-json",
                context={"status": resp.status_code, "text": resp.text[:500]},
            ) from e

        if not body.get("ok"):
            err = body.get("error") or {}
            raise JSRuntimeError(
                err.get("message", "sidecar exec failed"),
                context={"kind": err.get("kind"), "stack": err.get("stack")},
            )
        return body.get("result")

    async def health(self) -> bool:
        try:
            r = await self._client.get("/health", timeout=2.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
