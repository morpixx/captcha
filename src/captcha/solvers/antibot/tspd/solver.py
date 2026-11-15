"""F5 Shape (TSPD) solver.

Shape's VM mints the `TS*` cookie; there is nothing to assemble in Python. The
solver therefore does three things and no more:

1. run the tenant script under the capture harness (js-runtime),
2. take the `TS*` / `TSPD_101` cookie it wrote and replay the sensor POST it
   attempted -- towards the site (or the `tspd_url` host when the payload names
   one) and nowhere else (see :mod:`.cookies`),
3. hand back the cookie jar, which `domain/solution.py` projects onto the
   cookie-engine shape (TSPD additionally gets the capitalised `Domains` key a
   reference client reads).

Browser lane: navigate and wait for the cookie. Reached through
`NeedsBrowserError` when the payload has no script or the worker has no runtime.

Errors: no script -> `NeedsBrowserError`, VM ran but set nothing ->
`ChallengeExpiredError`, runtime failure -> `JSRuntimeError` (from the lane
helpers).
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import NeedsBrowserError, UnsolvableError
from captcha.engines import capture as capture_mod
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot import _runtime
from captcha.solvers.antibot._base import AntibotBase

from . import cookies, endpoints


class TSPDSolver(AntibotBase):
    """F5 Shape (TSPD): script capture, replay, browser fallback."""

    engine = AntibotEngine.TSPD

    info: ClassVar[SolverInfo] = SolverInfo(
        name="tspd",
        types=(CaptchaType.TSPD,),
        http=True,
        browser=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        site = _site(task, payload)
        script = _script(payload)
        if not script:
            raise NeedsBrowserError(
                "tspd: the Shape VM is the payload generator and its script is missing; escalate to the browser lane",
                context={"type": task.type.value, "site": site},
            )
        self.require_ctx(ctx, "sensors")
        ua = _user_agent(task, payload)
        tspd_url = _tspd_url(payload)
        builder, build_ctx = self.build_sensor(
            ctx,
            {**payload, "challenge_js": script},
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={"ua": ua, "tspd_url": tspd_url},
        )
        program = await builder.build(build_ctx)
        if not isinstance(program, str):
            raise UnsolvableError(
                "tspd builder returned no capture program",
                context={"type": type(program).__name__},
            )
        raw = await _runtime.run_script(
            ctx,
            engine="tspd",
            script=program,
            timeout_ms=_timeout_ms(task, payload),
        )
        captured = capture_mod.parse(raw, engine="tspd")
        jar: dict[str, str] = dict(captured.cookies)
        name, value = captured.cookie_with_prefix(endpoints.TSPD_COOKIE_PREFIXES)
        if value:
            jar[name] = value
        jar.update(await cookies.replay(ctx, captured, site=site, tspd_url=tspd_url))
        name, value = cookies.require(jar, site=site)
        return cookies.build_result(jar, name, value, started=started, ua=ua)

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        site = _site(task, payload)
        ua = _user_agent(task, payload)
        jar: dict[str, str] = dict(_runtime.cookies_to_dict(payload.get("cookies")))
        spec = _runtime.browser_spec(task, payload, label=f"tspd:{task.id}")
        async with _runtime.open_session(ctx, spec, solver="tspd") as session:
            await _runtime.navigate(session, site)
            name, value = await _runtime.wait_for_cookie(
                session,
                prefixes=endpoints.TSPD_COOKIE_PREFIXES,
                url=site,
                timeout_s=_timeout_s(task, payload),
            )
            if value:
                jar[name] = value
            # Shape sets a family per deployment (TSPD_101, TS01<hex>); the wait ends
            # on the first one, the client needs every TS* the page left.
            jar.update(await _runtime.session_cookies(session, site))
        name, value = cookies.require(jar, site=site)
        return cookies.build_result(jar, name, value, started=started, ua=ua, mode=SolveMode.BROWSER)


def _script(payload: dict[str, Any]) -> str:
    for key in ("challenge_js", "tspd_script", "script", "main_js"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _site(task: Task, payload: dict[str, Any]) -> str:
    for value in (task.site_url, payload.get("websiteURL"), payload.get("pageurl"), payload.get("url")):
        if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
            return value.strip()
    raise UnsolvableError(
        "tspd needs the protected page URL (websiteURL)",
        context={"type": task.type.value},
    )


def _tspd_url(payload: dict[str, Any]) -> str:
    for key in ("tspd_url", "endpoint", "sensor_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _user_agent(task: Task, payload: dict[str, Any]) -> str | None:
    value = task.user_agent or payload.get("userAgent") or payload.get("ua")
    return str(value) if value else None


def _timeout_s(task: Task, payload: dict[str, Any]) -> float:
    value = payload.get("timeout_s")
    seconds = float(value) if isinstance(value, int | float) and value >= 0 else 20.0
    deadline = getattr(task, "deadline_ts", None)
    if deadline:
        remaining = deadline - time.time()
        if remaining > 0:
            return min(seconds, remaining)
    return seconds


def _timeout_ms(task: Task, payload: dict[str, Any]) -> int:
    return max(1, int(_timeout_s(task, payload) * 1000))
