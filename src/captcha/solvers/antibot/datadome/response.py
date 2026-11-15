"""DataDome answer assembly.

Everything a DataDome round produces is a ``datadome`` cookie plus the UA that
minted it. This module turns that pair into a ``SolverResult`` whose ``extra``
``domain/solution.py`` can project onto the 2captcha ``cookies`` +
``domains: {host: {cookies}}`` shape, and reads ``Set-Cookie``/``body`` off a
transport response without importing the transport (``domain/ports.py`` does
not expose a Response type on purpose).
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain import SolveMode, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError

from . import endpoints
from . import payload as payload_fields


def headers(ua: str | None) -> dict[str, str]:
    return {"User-Agent": ua} if ua else {}


def origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def set_cookie(resp: Any) -> list[str]:
    """All Set-Cookie values; repeated headers must not collapse into one."""
    getter = getattr(resp, "headers_all", None)
    if callable(getter):
        return list(getter("set-cookie"))
    header = getattr(resp, "header", None)
    value = header("set-cookie") if callable(header) else None
    return [value] if value else []


def body(resp: Any) -> bytes | str | None:
    raw = getattr(resp, "body", None)
    return raw if isinstance(raw, (bytes, str)) else None


def build_result(
    payload: dict[str, Any],
    task: Task,
    cookie: str,
    *,
    started: float,
    mode: SolveMode = SolveMode.HTTP,
    ua: str | None = None,
    extra_headers: dict[str, str] | None = None,
    reason: str = "no datadome cookie obtained",
) -> SolverResult:
    """One cookie, one shape. A round without a cookie is a stale challenge."""
    cookie = str(cookie or "")
    if not cookie:
        raise ChallengeExpiredError(
            f"datadome: {reason}",
            context={"type": task.type.value, "mode": str(mode)},
        )
    merged = {**headers(ua or payload_fields.user_agent(task, payload)), **(extra_headers or {})}
    extra: dict[str, Any] = {
        "cookies": {endpoints.DATADOME_COOKIES[0]: cookie},
        "userAgent": ua or payload_fields.user_agent(task, payload),
        "domain": payload_fields.domain(task, payload),
    }
    if merged:
        extra["headers"] = {k: v for k, v in merged.items() if v}
    return SolverResult(
        token=cookie,
        extra=extra,
        solve_mode=mode,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )
