"""Imperva answer assembly.

Every Imperva round -- reese84 envelope or `___utmvc` script -- ends in a cookie
jar, and that jar is the answer: `reese84` plus the rotated `incap_ses_*` and
`visid_incap_*`. `domain/solution.py` projects the jar onto the 2captcha
`cookies` + `domains` shape (COOKIE_ENGINE), so this module only has to be
strict about *which* cookies are the answer and about never inventing one.

Reading a transport response without importing the transport: `domain/ports.py`
does not expose a `Response` type on purpose, so the read is duck-typed.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain import SolveMode, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError
from captcha.solvers.antibot import _runtime

from . import endpoints
from . import payload as payload_fields

#: Hosts Imperva's own sensor infrastructure uses; a captured POST may target them.
IMPERVA_VENDORS: tuple[str, ...] = ("incapsula.com", "impervadns.net", "imperva.com")


def headers(ua: str | None) -> dict[str, str]:
    return {"User-Agent": ua} if ua else {}


def set_cookie(resp: Any) -> list[str]:
    """All Set-Cookie values; repeated headers must not collapse into one."""
    return _runtime.set_cookie(resp)


def status_of(resp: Any) -> int:
    try:
        return int(getattr(resp, "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def is_imperva_cookie(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(prefix) for prefix in endpoints.IMPERVA_COOKIE_PREFIXES)


def imperva_cookies(raw: dict[str, str]) -> dict[str, str]:
    """Keep the cookies the WAF issued; drop whatever else the origin set."""
    return {name: value for name, value in raw.items() if value and is_imperva_cookie(name)}


def merge(jar: dict[str, str], raw: dict[str, str]) -> dict[str, str]:
    """Update the task jar with a response jar, filtering by cookie family."""
    jar.update(imperva_cookies(raw))
    return jar


def replayable(url: str, origin: str) -> bool:
    """Only replay a captured request back to the site or to Imperva itself."""
    return _runtime.replayable(url, origin, vendors=IMPERVA_VENDORS)


def absolute(url: str, origin: str) -> str:
    """Captured URLs are usually absolute; a path is resolved against the site."""
    return _runtime.absolute(url, origin)


def build_result(
    payload: dict[str, Any],
    task: Task,
    jar: dict[str, str],
    *,
    started: float,
    mode: SolveMode = SolveMode.HTTP,
    ua: str | None = None,
    reason: str = "no Imperva cookie obtained",
) -> SolverResult:
    """One jar, one shape. A round without an Imperva cookie is a stale challenge."""
    cookies = imperva_cookies(jar)
    if not cookies:
        raise ChallengeExpiredError(
            f"imperva: {reason}",
            context={"type": task.type.value, "mode": str(mode), "keys": sorted(jar)[:8]},
        )
    token = cookies.get(endpoints.IMPERVA_PRIMARY_COOKIE) or next(iter(cookies.values()))
    extra: dict[str, Any] = {
        "cookies": cookies,
        "userAgent": ua or payload_fields.user_agent(task, payload),
        "domain": payload_fields.domain(task, payload),
    }
    merged = headers(ua or payload_fields.user_agent(task, payload))
    if merged:
        extra["headers"] = merged
    return SolverResult(
        token=token,
        extra=extra,
        solve_mode=mode,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def _hostname(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url if "//" in url else "//" + url)
    except ValueError:
        return ""
    return (parsed.hostname or "").lower()


__all__ = [
    "absolute",
    "build_result",
    "headers",
    "imperva_cookies",
    "is_imperva_cookie",
    "merge",
    "replayable",
    "set_cookie",
    "status_of",
]
