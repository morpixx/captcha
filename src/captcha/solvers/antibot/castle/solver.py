"""Castle.io solver.

Castle mints `castle_request_token` on the client and nowhere else: the SDK makes
no network call, the token is packed from device/behavior signals, lives 120 s,
and is valid for exactly one request (docs.castle.io/docs/sdk-browser.md). That
decides the shape of this solver:

* **pass-through** -- a token the client already has (or a documented `test|...`
  mock) is validated and returned unchanged. Nothing is re-minted.
* **js-runtime** -- when the payload carries `castle.browser.js`, the builder's
  capture program runs it and the token is read out of storage or out of the
  request body the SDK built. A bundle that needs a real event loop produces
  nothing here, and that is reported as such.
* **browser lane** -- the only path that reliably mints a token: navigate the
  page, configure the SDK with the publishable key, call
  `createRequestToken()`, return the device id next to the token.

Errors: no publishable key / no page -> `UnsolvableError`, SDK present but minted
nothing -> `ChallengeExpiredError`, no runtime or no page to run the SDK ->
`NeedsBrowserError`.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError, NeedsBrowserError, UnsolvableError
from captcha.engines import capture as capture_mod
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot import _runtime
from captcha.solvers.antibot._base import AntibotBase

from . import endpoints, page
from . import payload as payload_fields


class CastleSolver(AntibotBase):
    """Castle.io request token: pass-through, JS capture, browser lane."""

    engine = AntibotEngine.CASTLE

    info: ClassVar[SolverInfo] = SolverInfo(
        name="castle",
        types=(CaptchaType.CASTLE,),
        http=True,
        browser=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        pk = payload_fields.publishable_key(task, payload)
        ua = payload_fields.user_agent(task, payload)

        supplied = payload_fields.supplied_token(payload)
        if supplied:
            token = page.validate_token(supplied, pk=pk)
            return self._result(token, started=started, ua=ua)

        script = payload_fields.challenge_js(payload)
        if not script or ctx.sensors is None or ctx.js_runtime is None:
            raise NeedsBrowserError(
                "castle: the token is minted client-side and needs a page; escalate to the browser lane",
                context={"type": task.type.value, "has_sdk_js": bool(script)},
            )
        try:
            token = await self._capture(task, ctx, payload, script, pk=pk, ua=ua)
        except ChallengeExpiredError as exc:
            raise NeedsBrowserError(
                "castle: the SDK produced no token in the JS runtime; it needs a live page",
                context={"type": task.type.value, "reason": exc.message},
            ) from exc
        return self._result(token, started=started, ua=ua)

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        url = payload_fields.page_url(task, payload)
        pk = payload_fields.publishable_key(task, payload)
        ua = payload_fields.user_agent(task, payload)
        sdk_url = payload_fields.sdk_url(payload, endpoints.CASTLE_SDK_URL)
        timeout_ms = int(payload_fields.cookie_timeout_s(task, payload) * 1000)

        spec = _runtime.browser_spec(task, payload, label=f"castle:{task.id}")
        async with _runtime.open_session(ctx, spec, solver="castle") as session:
            await _runtime.navigate(session, url)
            page_obj = await _runtime.page_of(session)
            report = await page.drive(page_obj, pk=pk, sdk_url=sdk_url, timeout_ms=timeout_ms)

        raw_token = report.get("token")
        if not isinstance(raw_token, str) or not raw_token.strip():
            raise ChallengeExpiredError(
                f"castle: page minted no token ({page.error_of(report) or 'empty'})",
                context={"cuid": report.get("cuid"), "url": url, "pk": pk},
            )
        token = page.validate_token(raw_token, pk=pk)
        return self._result(token, started=started, ua=ua, mode=SolveMode.BROWSER, cuid=report.get("cuid"))

    # -- helpers ----------------------------------------------------------

    async def _capture(
        self,
        task: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
        script: str,
        *,
        pk: str,
        ua: str | None,
    ) -> str:
        """Run castle.js under the capture harness and take the token it built."""
        builder, build_ctx = self.build_sensor(
            ctx,
            {**payload, "challenge_js": script},
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={"publishable_key": pk, "ua": ua, "storage": payload.get("storage")},
        )
        program = await builder.build(build_ctx)
        if not isinstance(program, str):
            raise UnsolvableError(
                "castle builder returned no capture program",
                context={"type": type(program).__name__},
            )
        raw = await _runtime.run_script(
            ctx,
            engine="castle",
            script=program,
            timeout_ms=int(payload_fields.cookie_timeout_s(task, payload) * 1000),
        )
        captured = capture_mod.parse(raw, engine="castle")
        token = captured.stored("castle_request_token") or captured.cookie("castle_request_token")
        if not token:
            for request in captured.requests:
                token = page.token_from_request(request.body)
                if token:
                    break
        if not token:
            reason = captured.error_tail() or "no token in storage, cookies or captured requests"
            raise ChallengeExpiredError(f"castle: {reason}", context={"pk": pk})
        return token

    def _result(
        self,
        token: str,
        *,
        started: float,
        ua: str | None,
        mode: SolveMode = SolveMode.HTTP,
        cuid: Any = None,
    ) -> SolverResult:
        extra: dict[str, Any] = {"userAgent": ua}
        if ua:
            extra["headers"] = {"User-Agent": ua}
        if isinstance(cuid, str) and cuid:
            extra["cuid"] = cuid
        return SolverResult(
            token=token,
            extra=extra,
            solve_mode=mode,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
