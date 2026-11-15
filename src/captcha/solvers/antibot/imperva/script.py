"""Script path for Imperva: run the captured generator, replay what it sent.

The reese / `main.js` source is the only artifact that knows how to produce
`___utmvc` or a rotated `reese84` for a given build. Running it under
:mod:`captcha.engines.capture` gives cookies plus the sensor requests it
attempted; replaying those through `ctx.http` -- and only towards the site or
Imperva itself -- turns them into the `Set-Cookie` the client needs.

Nothing here creates a runtime or a client: both arrive in `SolverContext`.
"""

from __future__ import annotations

from typing import Any

from captcha.domain.errors import ChallengeExpiredError
from captcha.engines import capture as capture_mod
from captcha.solvers.antibot import _runtime
from captcha.solvers.antibot._base import AntibotBase

from . import endpoints
from . import response as answer


async def run(
    ctx: Any,
    *,
    site: str,
    program: str,
    jar: dict[str, str],
    timeout_ms: int,
) -> tuple[dict[str, str], str]:
    """Execute the script, then replay its POSTs; returns `(jar, reason)`.

    `reason` is empty when the jar ended up with an Imperva cookie; otherwise it
    says what went wrong, including the capture's own error tail when the script
    threw, so a failure is diagnosable from the task error alone.
    """
    raw = await _runtime.run_script(ctx, engine="imperva", script=program, timeout_ms=timeout_ms)
    captured = capture_mod.parse(raw, engine="imperva")
    merged = dict(jar)
    merged.update(captured.cookies)
    http = getattr(ctx, "http", None)
    for request in captured.requests:
        if not request.is_post:
            continue
        url = _runtime.absolute(request.url, site)
        if not _runtime.replayable(url, site, vendors=answer.IMPERVA_VENDORS):
            continue
        try:
            resp = await http.request(request.method, url, headers=request.headers or None, data=request.body)
        except ChallengeExpiredError:
            raise
        except Exception:  # a sensor POST the site drops must not lose the cookie we hold
            continue
        answer.merge(merged, AntibotBase.parse_cookies(_runtime.set_cookie(resp)))
        if merged.get(endpoints.IMPERVA_PRIMARY_COOKIE):
            break
    reason = ""
    if not answer.imperva_cookies(merged):
        reason = captured.error_tail() or "script produced no Imperva cookie"
    return merged, reason


__all__ = ["run"]
