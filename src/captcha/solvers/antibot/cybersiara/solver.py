"""CyberSiARA solver.

The widget's answer is one token (a master URL token). Three things can produce it:

* **pass-through** -- the client already holds a token; it is validated, not
  re-minted.
* **script capture** (js-runtime) -- the widget script runs under the capture
  harness; the token is taken from storage, a cookie, a captured request body,
  or the answer of the verify call the widget made (replayed through
  `ctx.http` towards the site or `cybersiara.com` only).
* **browser lane** -- the page mints it after a human-shaped slide; the solver
  navigates, polls the storage/cookie/global harvest and returns what it finds.
* nothing else: the verification body is not public, so a "pure HTTP" assembly
  would be a guess dressed as a solver.

Errors: no site key -> `UnsolvableError`, widget ran but left no token ->
`ChallengeExpiredError`, no script and no runtime -> `NeedsBrowserError`.
"""

from __future__ import annotations

import asyncio
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


class CyberSiARASolver(AntibotBase):
    """CyberSiARA: token pass-through, widget capture, browser harvest."""

    engine = AntibotEngine.CYBERSIARA

    info: ClassVar[SolverInfo] = SolverInfo(
        name="cybersiara",
        types=(CaptchaType.CYBERSIARA,),
        http=True,
        browser=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        key = payload_fields.sitekey(task, payload)
        ua = payload_fields.user_agent(task, payload)

        supplied = payload_fields.supplied_token(payload)
        if supplied:
            return self._result(page.validate_token(supplied, sitekey=key), started=started, ua=ua)

        script = payload_fields.script(payload)
        if not script or ctx.sensors is None or ctx.js_runtime is None:
            raise NeedsBrowserError(
                "cybersiara: the token is minted by the widget script; escalate to the browser lane",
                context={"type": task.type.value, "has_script": bool(script)},
            )
        token = await self._capture(task, ctx, payload, script, sitekey=key, ua=ua)
        return self._result(page.validate_token(token, sitekey=key), started=started, ua=ua)

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()
        key = payload_fields.sitekey(task, payload)
        url = payload_fields.site(task, payload)
        ua = payload_fields.user_agent(task, payload)
        budget = payload_fields.timeout_s(task, payload)

        spec = _runtime.browser_spec(task, payload, label=f"cybersiara:{task.id}")
        report: dict[str, Any] = {}
        deadline = time.monotonic() + budget
        async with _runtime.open_session(ctx, spec, solver="cybersiara") as session:
            await _runtime.navigate(session, url)
            page_obj = await _runtime.page_of(session)
            while True:
                report = await page.harvest(
                    page_obj,
                    sitekey=key,
                    cookie_prefixes=endpoints.CYBERSIARA_COOKIE_PREFIXES,
                )
                if isinstance(report.get("token"), str) and report["token"].strip():
                    break
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.5)

        raw_token = report.get("token")
        if not isinstance(raw_token, str) or not raw_token.strip():
            raise ChallengeExpiredError(
                f"cybersiara: the page left no token ({report.get('error') or 'empty'})",
                context={"sitekey": key, "url": url, "diag": report.get("diag")},
            )
        token = page.validate_token(raw_token, sitekey=key)
        return self._result(token, started=started, ua=ua, mode=SolveMode.BROWSER)

    # -- helpers ----------------------------------------------------------

    async def _capture(
        self,
        task: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
        script: str,
        *,
        sitekey: str,
        ua: str | None,
    ) -> str:
        builder, build_ctx = self.build_sensor(
            ctx,
            {**payload, "challenge_js": script},
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={"sitekey": sitekey, "ua": ua, "storage": payload.get("storage")},
        )
        program = await builder.build(build_ctx)
        if not isinstance(program, str):
            raise UnsolvableError(
                "cybersiara builder returned no capture program",
                context={"type": type(program).__name__},
            )
        raw = await _runtime.run_script(
            ctx,
            engine="cybersiara",
            script=program,
            timeout_ms=int(payload_fields.timeout_s(task, payload) * 1000),
        )
        captured = capture_mod.parse(raw, engine="cybersiara")
        token = captured.stored(*page.STORAGE_KEYS) or captured.cookie(*page.STORAGE_KEYS)
        if token:
            return token
        for request in captured.requests:
            token = page.token_from_payload(request.body)
            if token:
                return token
        token = await self._replay(ctx, captured, payload_fields.site(task, payload))
        if token:
            return token
        reason = captured.error_tail() or "no token in storage, cookies or captured requests"
        raise ChallengeExpiredError(f"cybersiara: {reason}", context={"sitekey": sitekey})

    async def _replay(self, ctx: SolverContext, captured: capture_mod.Capture, site: str) -> str:
        """Replay the widget's verify call; the answer may carry the token."""
        http = getattr(ctx, "http", None)
        if http is None:
            return ""
        for request in captured.requests:
            if not request.is_post:
                continue
            url = _runtime.absolute(request.url, site)
            if not _runtime.replayable(url, site, vendors=endpoints.CYBERSIARA_VENDORS):
                continue
            try:
                resp = await http.request(request.method, url, headers=request.headers or None, data=request.body)
            except ChallengeExpiredError:
                raise
            except Exception:  # a rejected verify call is not fatal: the token may be local
                continue
            token = page.token_from_payload(getattr(resp, "body", b""))
            if token:
                return token
        return ""

    def _result(
        self,
        token: str,
        *,
        started: float,
        ua: str | None,
        mode: SolveMode = SolveMode.HTTP,
    ) -> SolverResult:
        extra: dict[str, Any] = {"userAgent": ua}
        if ua:
            extra["headers"] = {"User-Agent": ua}
        return SolverResult(
            token=token,
            extra=extra,
            solve_mode=mode,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
