"""PerimeterX answer assembly.

A collector round answers `{"ob": "<base64>"}`: the blob decodes to a
`~~~~`-separated segment list (:func:`captcha.engines.perimeterx.codec.parse_ob`)
whose cookie-shaped names are the `_px*` cookies to bake. Some deployments also
send `Set-Cookie`, and the press path produces cookies directly -- all three
end in one jar, which is the only thing a client can use on its own request.

`domain/solution.py` projects the jar onto the 2captcha `cookies` + `domains`
shape (COOKIE_ENGINE); this module's job is to be strict about which cookies are
the answer and never to invent one.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain import SolveMode, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError
from captcha.engines.perimeterx import codec
from captcha.solvers.antibot import _runtime

from . import endpoints
from . import payload as payload_fields


def headers(ua: str | None) -> dict[str, str]:
    return {"User-Agent": ua} if ua else {}


def origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""


def set_cookie(resp: Any) -> list[str]:
    """All Set-Cookie values; repeated headers must not collapse into one."""
    return _runtime.set_cookie(resp)


def status_of(resp: Any) -> int:
    try:
        return int(getattr(resp, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def body(resp: Any) -> bytes | str:
    raw = getattr(resp, "body", b"")
    return raw if isinstance(raw, bytes | str) else b""


def ob(resp: Any) -> str:
    """The `ob` blob of a collector answer, when the body carries one."""
    raw = body(resp)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    text = text.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return ""
    if isinstance(parsed, dict):
        value = parsed.get("ob")
        if isinstance(value, str):
            return value
        for key in ("payload", "data"):
            nested = parsed.get(key)
            if isinstance(nested, str):
                return nested
    return ""


def cookies_from_ob(blob: str) -> dict[str, str]:
    """Decode an `ob` blob into the cookies it bakes."""
    if not blob:
        return {}
    return codec.cookies_from_ob(codec.parse_ob(blob))


def px_cookies(raw: dict[str, str]) -> dict[str, str]:
    """Keep PX's own cookies; drop anything else the origin set."""
    return {
        name: value
        for name, value in raw.items()
        if value and (name in endpoints.PX_COOKIES or name.startswith(endpoints.PX_COOKIE_PREFIXES))
    }


def merge(jar: dict[str, str], *raw: dict[str, str]) -> dict[str, str]:
    for item in raw:
        jar.update(px_cookies(item))
    return jar


def pick(jar: dict[str, str]) -> tuple[str, str]:
    """`_pxhd` (the verdict) first, then the sensor token."""
    for name in endpoints.PX_PRIMARY_COOKIES:
        if jar.get(name):
            return name, jar[name]
    for name, value in jar.items():
        if value and name.startswith(endpoints.PX_COOKIE_PREFIXES):
            return name, value
    return "", ""


def build_result(
    payload: dict[str, Any],
    task: Task,
    jar: dict[str, str],
    *,
    started: float,
    mode: SolveMode = SolveMode.HTTP,
    ua: str | None = None,
    reason: str = "no PerimeterX cookie obtained",
) -> SolverResult:
    """One jar, one shape. A round without a PX cookie is a stale challenge."""
    cookies = px_cookies(jar)
    value = pick(cookies)[1]
    if not value:
        raise ChallengeExpiredError(
            f"perimeterx: {reason}",
            context={"type": task.type.value, "mode": str(mode), "keys": sorted(jar)[:8]},
        )
    extra: dict[str, Any] = {
        "cookies": cookies,
        "userAgent": ua or payload_fields.user_agent(task, payload),
        "domain": payload_fields.domain(task, payload),
    }
    merged = headers(ua or payload_fields.user_agent(task, payload))
    if merged:
        extra["headers"] = merged
    return SolverResult(
        token=value,
        extra=extra,
        solve_mode=mode,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


__all__ = [
    "body",
    "build_result",
    "cookies_from_ob",
    "headers",
    "merge",
    "ob",
    "origin",
    "pick",
    "px_cookies",
    "set_cookie",
    "status_of",
]
