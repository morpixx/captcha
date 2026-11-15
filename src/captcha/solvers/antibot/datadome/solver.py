"""DataDome solver.

Three paths, one answer shape. Every DataDome verdict a client can use is a
``datadome`` cookie, so all three paths end in a ``SolverResult`` carrying
``{"cookies": {"datadome": ...}, "userAgent": ..., "domain": ...}``; ``domain/
solution.py`` projects that onto the 2captcha ``cookies`` + ``domains`` shape
(``docs/protocol.md``).

1. **tags.js** (HTTP-only, ``DATADOME_FULL`` with a ``ddjskey``): build ``jspl``
   locally (``tags.py``), POST to ``api-js.datadome.co/js/``, read the cookie
   from ``Set-Cookie`` or the JSON body.
2. **Device Check / interstitial** (js-runtime): run the captured challenge
   script in ``ctx.js_runtime`` and take the cookies/headers it returns.
3. **Browser lane**: drive the real page through ``ctx.browser`` and read the
   cookie out of the session jar. Reached only by a ``NeedsBrowserError``
   escalation; the HTTP solver never opens a browser itself.

Errors: missing input -> ``UnsolvableError``, rejected sensor -> ``SensorError``,
no cookie -> ``ChallengeExpiredError``, needs page/runtime -> ``NeedsBrowserError``.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import NeedsBrowserError, SensorError, UnsolvableError
from captcha.domain.ports import BrowserSpec
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase

from . import endpoints, interstitial, tags
from . import payload as payload_fields
from . import response as answer


class DataDomeSolver(AntibotBase):
    """DataDome — full (tags.js) and slider variants."""

    engine = AntibotEngine.DATADOME

    info: ClassVar[SolverInfo] = SolverInfo(
        name="datadome",
        types=(CaptchaType.DATADOME_FULL, CaptchaType.DATADOME_SLIDER),
        http=True,
        browser=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()

        cookie = payload_fields.payload_cookie(payload)
        if cookie:
            return answer.build_result(payload, task, cookie, started=started)
        if task.type is CaptchaType.DATADOME_SLIDER:
            return await self._device_check(task, ctx, payload, started=started)
        if task.type is CaptchaType.DATADOME_FULL:
            ddjskey = payload_fields.ddjskey(task, payload)
            if ddjskey:
                return await self._tags(task, ctx, payload, ddjskey, started=started)
            return await self._device_check(task, ctx, payload, started=started)
        raise UnsolvableError(
            f"datadome solver got unexpected type: {task.type.value}",
            context={"type": task.type.value},
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = self.payload_or_fail(task)
        started = time.perf_counter()

        cookie = payload_fields.payload_cookie(payload)
        if cookie:
            return answer.build_result(payload, task, cookie, started=started, mode=SolveMode.BROWSER)
        url = payload_fields.page_url(task, payload)
        ua = payload_fields.user_agent(task, payload)
        spec = BrowserSpec(
            proxy=task.proxy,
            cookies=interstitial.cookie_spec(payload.get("cookies")),
            label=f"datadome:{task.id}",
        )
        async with interstitial.open_browser(ctx, spec) as session:
            await interstitial.navigate(session, url)
            jar = await interstitial.wait_for_cookie(
                session,
                url=url,
                timeout_s=payload_fields.cookie_timeout_s(task, payload),
            )
        return answer.build_result(
            payload,
            task,
            jar.get(endpoints.DATADOME_COOKIES[0], ""),
            started=started,
            mode=SolveMode.BROWSER,
            ua=ua,
            extra_headers=answer.headers(ua),
            reason="browser jar had no datadome cookie",
        )

    # -- paths ------------------------------------------------------------

    async def _tags(
        self,
        task: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
        ddjskey: str,
        *,
        started: float,
    ) -> SolverResult:
        """HTTP-only tags.js round: jspl in, datadome cookie out."""
        self.require_ctx(ctx, "http")
        url = payload_fields.page_url(task, payload)
        ua = payload_fields.user_agent(task, payload)
        if not ua:
            raise UnsolvableError(
                "datadome tags.js needs userAgent: the payload is scored against it",
                context={"keys": sorted(payload)},
            )
        jar = interstitial.cookies_to_dict(payload.get("cookies"))
        body = tags.build_tags_body(
            ddjskey=ddjskey,
            cid=str(payload.get("cid") or jar.get(endpoints.DATADOME_COOKIES[0]) or ""),
            hsh=str(payload.get("hsh") or ""),
            page_url=url,
            user_agent=ua,
            signals=payload_fields.signals(payload),
            js_type=payload_fields.js_type(task),
            timestamp_ms=payload_fields.timestamp_ms(payload),
        )
        resp = await self.post_sensor(
            ctx,
            endpoints.DATADOME_JS_ENDPOINT,
            body,
            headers={
                "User-Agent": ua,
                "Content-Type": "application/json",
                "Referer": url,
                "Origin": answer.origin(url),
            },
            cookies=jar or None,
        )
        status = int(getattr(resp, "status", 0) or 0)
        if status == 0 or status >= 400:
            raise SensorError(
                "datadome tags endpoint rejected the payload",
                context={"status": status, "ddk": ddjskey, "url": endpoints.DATADOME_JS_ENDPOINT},
            )
        jar.update(self.parse_cookies(answer.set_cookie(resp)))
        jar.update(tags.datadome_from_body(answer.body(resp)))
        return answer.build_result(
            payload,
            task,
            jar.get(endpoints.DATADOME_COOKIES[0], ""),
            started=started,
            ua=ua,
            extra_headers=answer.headers(ua),
            reason="tags endpoint accepted the payload but set no datadome cookie",
        )

    async def _device_check(
        self,
        task: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
        *,
        started: float,
    ) -> SolverResult:
        """Run the interstitial script in the JS runtime and take its cookies."""
        script = payload.get("challenge_js") or payload.get("challenge_script")
        if not script:
            raise NeedsBrowserError(
                "datadome device check needs the interstitial page; escalate to the browser lane",
                context={"type": task.type.value, "keys": sorted(payload)},
            )
        if getattr(ctx, "js_runtime", None) is None:
            raise NeedsBrowserError(
                "datadome device check needs a JS runtime; escalate to the browser lane",
                context={"type": task.type.value},
            )
        out = await interstitial.run_challenge_js(
            ctx,
            script=str(script),
            globals_=payload_fields.js_globals(task, payload),
            timeout_ms=payload_fields.js_timeout_ms(task, payload),
        )
        ua = out.user_agent or payload_fields.user_agent(task, payload)
        return answer.build_result(
            payload,
            task,
            out.cookie,
            started=started,
            ua=ua,
            extra_headers={**answer.headers(ua), **out.headers},
            reason="challenge script produced no datadome cookie",
        )
