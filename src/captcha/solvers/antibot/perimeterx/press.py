"""Press / bundle path for the PerimeterX solver.

The press deployment ships `/assets/js/bundle` with WASM and a PoW: the bundle
computes its own constants and posts to the collector itself, so no amount of
Python assembly reproduces it. Running it under
:mod:`captcha.engines.capture` gives the cookies it baked plus the collector
request it built; replaying that request through `ctx.http` -- towards
PerimeterX or the site only -- turns the `ob` blob into the cookie jar the client
needs.

Nothing here creates a runtime or a client: both arrive in `SolverContext`.
"""

from __future__ import annotations

from typing import Any

from captcha.domain import SolverResult, Task
from captcha.engines import capture as capture_mod
from captcha.solvers.antibot import _runtime
from captcha.solvers.antibot._base import AntibotBase

from . import endpoints
from . import response as answer


async def run(
    ctx: Any,
    *,
    task: Task,
    payload: dict[str, Any],
    page: str,
    program: str,
    ua: str | None,
    jar: dict[str, str],
    timeout_ms: int,
    started: float,
) -> SolverResult:
    """Execute the bundle, then replay the collector call it made."""
    raw = await _runtime.run_script(ctx, engine="perimeterx", script=program, timeout_ms=timeout_ms)
    captured = capture_mod.parse(raw, engine="perimeterx")
    merged = dict(jar)
    merged.update(answer.px_cookies(captured.cookies))
    http = getattr(ctx, "http", None)
    for request in captured.requests:
        if http is None:
            break
        url = _runtime.absolute(request.url, page)
        if not _runtime.replayable(url, page, vendors=endpoints.PX_VENDORS):
            continue
        try:
            resp = await http.request(request.method, url, headers=request.headers or None, data=request.body)
        except Exception:  # a collector call the site drops must not lose a cookie we hold
            continue
        answer.merge(
            merged,
            AntibotBase.parse_cookies(_runtime.set_cookie(resp)),
            answer.cookies_from_ob(answer.ob(resp)),
        )
        if answer.pick(merged)[1]:
            break
    reason = "press bundle produced no PerimeterX cookie"
    if captured.errors:
        reason = f"{reason} ({captured.error_tail()[:200]})"
    return answer.build_result(payload, task, merged, started=started, ua=ua, reason=reason)


__all__ = ["run"]
