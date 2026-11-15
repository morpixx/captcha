"""Cookie handling for F5 Shape (TSPD) answers.

A Shape round produces one of two cookie families and nothing else worth
returning: `TSPD_101` under BIG-IP Bot Defense, `TS01<hex>` under proactive bot
defense, and the customer may rename either prefix. So the name is discovered,
never assumed, and the answer carries every `TS*` cookie the round produced --
a client replaying only one of them re-enters the challenge.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import SolveMode, SolverResult
from captcha.domain.errors import ChallengeExpiredError
from captcha.engines import capture as capture_mod
from captcha.solvers.antibot import _runtime
from captcha.solvers.antibot._base import AntibotBase

from . import endpoints


def pick(jar: dict[str, str]) -> tuple[str, str]:
    """First `TS*`/`TSPD_101` cookie in the jar, or `("", "")`."""
    for name, value in jar.items():
        if value and endpoints.is_ts_cookie(name):
            return name, value
    return "", ""


async def replay(
    ctx: Any,
    captured: capture_mod.Capture,
    *,
    site: str,
    tspd_url: str,
) -> dict[str, str]:
    """Send the sensor POST the VM built; return the cookies it answered with.

    Only the site itself and the `tspd_url` host are legal targets: the script is
    untrusted input and must not be able to aim our transport at a third party.
    """
    http = getattr(ctx, "http", None)
    if http is None:
        return {}
    origins = [site]
    if tspd_url:
        origins.append(_runtime.absolute(tspd_url, site))
    jar: dict[str, str] = {}
    for request in captured.requests:
        if not request.is_post:
            continue
        url = _runtime.absolute(request.url, site)
        if not any(_runtime.replayable(url, origin) for origin in origins):
            continue
        try:
            resp = await http.request(request.method, url, headers=request.headers or None, data=request.body)
        except ChallengeExpiredError:
            raise
        except Exception:  # a rejected sensor POST must not discard a cookie we already hold
            continue
        jar.update(AntibotBase.parse_cookies(_runtime.set_cookie(resp)))
        if pick(jar)[1]:
            break
    return jar


def build_result(
    jar: dict[str, str],
    name: str,
    value: str,
    *,
    started: float,
    ua: str | None,
    mode: SolveMode = SolveMode.HTTP,
) -> SolverResult:
    """Cookie-engine shape: every `TS*` cookie plus the UA that minted it."""
    ts = {cookie: val for cookie, val in jar.items() if endpoints.is_ts_cookie(cookie) and val}
    extra: dict[str, Any] = {"cookies": ts or {name: value}, "userAgent": ua}
    if ua:
        extra["headers"] = {"User-Agent": ua}
    return SolverResult(
        token=value,
        extra=extra,
        solve_mode=mode,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )


def require(jar: dict[str, str], *, site: str) -> tuple[str, str]:
    """Pick the cookie or raise: a Shape round without one is a stale challenge."""
    name, value = pick(jar)
    if not value:
        raise ChallengeExpiredError(
            "tspd: the round produced no TS cookie",
            context={"site": site, "cookies": sorted(jar)[:8]},
        )
    return name, value


__all__ = ["build_result", "pick", "replay", "require"]
