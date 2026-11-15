"""PerimeterX / HUMAN solver.

Two round types, one answer shape (a `_px*` cookie jar; `_pxhd` is the verdict
a client replays):

1. **sensorless collector** (HTTP-first, docs/reverse-2026.md §7). The builder
   assembles `appId, tag, ft, seq, en, uuid, vid, cts, sid, pc, payload` and this
   solver POSTs it form-encoded to the tenant collector. The `ob` blob in the
   answer bakes the cookies.
2. **press / bundle** (js-runtime). The bundle is the generator: it runs under
   the capture harness and the collector call it made is replayed
   (:mod:`.press`) -- towards PerimeterX or the site only, so an
   attacker-supplied script cannot aim our transport elsewhere.

Browser lane: navigate and wait for a `_px*` cookie (px3 interstitials).

Errors: no appId / no page -> `UnsolvableError`, collector rejected the body ->
`SensorError`, no cookie -> `ChallengeExpiredError`, no runtime ->
`NeedsBrowserError`.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar
from urllib.parse import urlencode

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot import _runtime
from captcha.solvers.antibot._base import AntibotBase

from . import endpoints
from . import payload as payload_fields
from . import press as press_path
from . import response as answer


class PerimeterXSolver(AntibotBase):
    """PerimeterX (HUMAN Security): sensorless collector, press capture, browser."""

    engine = AntibotEngine.PERIMETERX

    info: ClassVar[SolverInfo] = SolverInfo(
        name="perimeterx",
        types=(CaptchaType.PERIMETERX,),
        http=True,
        browser=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        app_id = payload_fields.app_id(task, payload)
        page = payload_fields.site_url(task, payload)
        ua = payload_fields.user_agent(task, payload)
        jar = payload_fields.cookies(payload)

        if payload_fields.script(payload):
            return await self._press(task, ctx, payload, page, started=started, ua=ua, jar=jar)
        return await self._collector(task, ctx, payload, page, app_id, started=started, ua=ua, jar=jar)

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        page = payload_fields.site_url(task, payload)
        ua = payload_fields.user_agent(task, payload)
        jar = payload_fields.cookies(payload)
        spec = _runtime.browser_spec(task, payload, label=f"perimeterx:{task.id}")
        async with _runtime.open_session(ctx, spec, solver="perimeterx") as session:
            await _runtime.navigate(session, page)
            name, value = await _runtime.wait_for_cookie(
                session,
                prefixes=endpoints.PX_COOKIE_PREFIXES,
                url=page,
                timeout_s=payload_fields.cookie_timeout_s(task, payload),
            )
            if value:
                jar[name] = value
            # The wait ends on the first _px* cookie, usually _pxvid; the verdict
            # (_pxhd) and the sensor token (_px) are what a client replays, so the
            # whole jar is read and response.pick chooses.
            jar.update(answer.px_cookies(await _runtime.session_cookies(session, page)))
        return answer.build_result(
            payload,
            task,
            jar,
            started=started,
            mode=SolveMode.BROWSER,
            ua=ua,
            reason="browser session never set a PerimeterX cookie",
        )

    # -- paths ------------------------------------------------------------

    async def _collector(
        self,
        task: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
        page: str,
        app_id: str,
        *,
        started: float,
        ua: str | None,
        jar: dict[str, str],
    ) -> SolverResult:
        """Sensorless round: one form POST, cookies out of the `ob` answer."""
        self.require_ctx(ctx, "http", "sensors")
        form = await self._form(task, ctx, payload, app_id, ua=ua, jar=jar)
        if isinstance(form, str):  # payload carried a script under another name
            return await press_path.run(
                ctx,
                task=task,
                payload=payload,
                page=page,
                program=form,
                ua=ua,
                jar=jar,
                timeout_ms=payload_fields.js_timeout_ms(task, payload),
                started=started,
            )

        url = endpoints.collector_url(app_id, explicit=payload_fields.collector_override(payload))
        resp = await ctx.http.post(
            url,
            data=urlencode(form),
            headers={
                **answer.headers(ua),
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": page,
                "Origin": answer.origin(page),
            },
            cookies=dict(jar) or None,
        )
        status = answer.status_of(resp)
        if status == 0 or status >= 400:
            raise SensorError(
                "perimeterx collector rejected the sensor body",
                context={"status": status, "url": url, "app_id": app_id},
            )
        jar = answer.merge(
            jar,
            self.parse_cookies(answer.set_cookie(resp)),
            answer.cookies_from_ob(answer.ob(resp)),
        )
        return answer.build_result(payload, task, jar, started=started, ua=ua)

    async def _press(
        self,
        task: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
        page: str,
        *,
        started: float,
        ua: str | None,
        jar: dict[str, str],
    ) -> SolverResult:
        """Bundle path: build the capture program, then let :mod:`.press` run it."""
        self.require_ctx(ctx, "sensors")
        program = await self._form(
            task,
            ctx,
            {**payload, "challenge_js": payload_fields.script(payload)},
            payload_fields.app_id(task, payload),
            ua=ua,
            jar=jar,
        )
        if not isinstance(program, str):
            raise SensorError(
                "perimeterx builder returned no capture program for a script payload",
                context={"type": type(program).__name__},
            )
        return await press_path.run(
            ctx,
            task=task,
            payload=payload,
            page=page,
            program=program,
            ua=ua,
            jar=jar,
            timeout_ms=payload_fields.js_timeout_ms(task, payload),
            started=started,
        )

    async def _form(
        self,
        task: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
        app_id: str,
        *,
        ua: str | None,
        jar: dict[str, str],
    ) -> dict[str, Any] | str:
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=jar,
            headers=payload.get("headers"),
            extra=payload_fields.builder_extra(task, payload, app_id, ua=ua),
        )
        return await builder.build(build_ctx)
