"""Imperva / Incapsula solver.

Two ways to the same answer -- a cookie jar with `reese84` and the rotated
`incap_ses_*` / `visid_incap_*`:

1. **reese84 envelope** (HTTP-first). The builder assembles
   `{"solution": {...}, "old_token": ..., "error": null, "performance": {...}}` and
   this solver POSTs it to `/_Incapsula_Resource?SWKMTFSR=1&e=<nonce>` on the
   protected origin; the origin answers with the cookies the client needs.
2. **script capture** (js-runtime). When the payload carries the reese or
   `main.js` source, the script *is* the generator: run it under the capture
   harness, take the cookies it writes, and replay the sensor POST it attempts --
   through `ctx.http` and only towards the site or Imperva itself, so an
   attacker-supplied script cannot steer our transport at another host
   (:mod:`.script` owns that path).

Browser lane: navigate the origin and wait for the cookie. Reached through
`NeedsBrowserError` when a worker has no JS runtime, or by the router.

Errors: missing origin -> `UnsolvableError`, rejected envelope -> `SensorError`,
no cookie -> `ChallengeExpiredError`, no runtime -> `NeedsBrowserError`.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot import _runtime
from captcha.solvers.antibot._base import AntibotBase

from . import endpoints
from . import payload as payload_fields
from . import response as answer
from . import script as script_path


class ImpervaSolver(AntibotBase):
    """Imperva (Incapsula): reese84 envelope, script capture, browser fallback."""

    engine = AntibotEngine.IMPERVA

    info: ClassVar[SolverInfo] = SolverInfo(
        name="imperva",
        types=(CaptchaType.IMPERVA,),
        http=True,
        browser=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        site = payload_fields.origin(task, payload)
        ua = payload_fields.user_agent(task, payload)
        jar = payload_fields.payload_cookies(payload)
        if payload_fields.challenge_js(payload):
            return await self._capture(task, ctx, payload, site, started=started, ua=ua, jar=jar)
        return await self._sensor(task, ctx, payload, site, started=started, ua=ua, jar=jar)

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        site = payload_fields.origin(task, payload)
        ua = payload_fields.user_agent(task, payload)
        jar = payload_fields.payload_cookies(payload)
        spec = _runtime.browser_spec(task, payload, label=f"imperva:{task.id}")
        async with _runtime.open_session(ctx, spec, solver="imperva") as session:
            await _runtime.navigate(session, site)
            name, value = await _runtime.wait_for_cookie(
                session,
                prefixes=endpoints.IMPERVA_COOKIE_PREFIXES,
                url=site,
                timeout_s=payload_fields.cookie_timeout_s(task, payload),
            )
            if value:
                jar[name] = value
            # The wait ends on the first Imperva cookie; the client needs the rest of
            # the family too (incap_ses_*, visid_incap_* rotate together).
            answer.merge(jar, await _runtime.session_cookies(session, site))
        return answer.build_result(
            payload,
            task,
            jar,
            started=started,
            mode=SolveMode.BROWSER,
            ua=ua,
            reason="browser session never set an Imperva cookie",
        )

    # -- paths ------------------------------------------------------------

    async def _sensor(
        self,
        task: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
        site: str,
        *,
        started: float,
        ua: str | None,
        jar: dict[str, str],
    ) -> SolverResult:
        """reese84 envelope: one POST, cookies in the answer."""
        self.require_ctx(ctx, "http", "sensors")
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            timestamp_ms=payload_fields.js_timeout_ms(task, payload),
            cookies=jar,
            headers=payload.get("headers"),
            extra={"ua": ua, "version": payload_fields.version(payload)},
        )
        body = await builder.build(build_ctx)
        if isinstance(body, str):  # payload carried a script under another name
            merged, reason = await script_path.run(
                ctx,
                site=site,
                program=body,
                jar=jar,
                timeout_ms=payload_fields.js_timeout_ms(task, payload),
            )
            return answer.build_result(payload, task, merged, started=started, ua=ua, reason=reason)

        url = endpoints.resource_url(site, payload_fields.nonce(payload))
        resp = await ctx.http.post(
            url,
            json=body,
            headers={
                **answer.headers(ua),
                "Content-Type": "application/json",
                "Referer": site + "/",
                "Origin": site,
            },
            cookies=dict(jar) or None,
        )
        status = answer.status_of(resp)
        if status == 0 or status >= 400:
            raise SensorError(
                "imperva rejected the reese84 envelope",
                context={"status": status, "url": url, "solution_keys": sorted(body.get("solution", {}))},
            )
        answer.merge(jar, self.parse_cookies(answer.set_cookie(resp)))
        return answer.build_result(payload, task, jar, started=started, ua=ua)

    async def _capture(
        self,
        task: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
        site: str,
        *,
        started: float,
        ua: str | None,
        jar: dict[str, str],
    ) -> SolverResult:
        """Build the capture program through the engine builder, then run it."""
        self.require_ctx(ctx, "sensors")
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            timestamp_ms=payload_fields.js_timeout_ms(task, payload),
            cookies=jar,
            headers=payload.get("headers"),
            extra={"ua": ua, "version": payload_fields.version(payload)},
        )
        program = await builder.build(build_ctx)
        if not isinstance(program, str):
            raise SensorError(
                "imperva builder returned no capture program for a script payload",
                context={"type": type(program).__name__},
            )
        merged, reason = await script_path.run(
            ctx,
            site=site,
            program=program,
            jar=jar,
            timeout_ms=payload_fields.js_timeout_ms(task, payload),
        )
        return answer.build_result(payload, task, merged, started=started, ua=ua, reason=reason)
