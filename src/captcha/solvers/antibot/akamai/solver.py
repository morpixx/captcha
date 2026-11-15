"""Akamai Bot Manager: the classic sensor_data flow (HTTP lane, JS runtime).

The handshake, as far as it is observable (2026-06 write-up; docs/reverse-2026.md
has no Akamai section yet):

    GET  <page>              -> Set-Cookie bm_sz (seed) and _abck (~-1~, waiting)
    POST <sensor script url> -> {"sensor_data": "<opaque>"} (form on 2.x tenants)
                             -> Set-Cookie _abck (~0~-1~ then ~0~)

The verdict is the _abck tail, not the status code: a 200 that leaves the cookie
intermediate is a failed task (ChallengeExpiredError, retried or DLQed), and a
tenant that never settles is reported with the markers it actually returned. The
sensor body is built by the engine builder, which runs the tenant's own script in
the worker's V8 runtime -- this solver only transports and grades.

Session state comes from payload mode first (docs/protocol.md): cookies, and the
script when the client already fetched it. The bootstrap GET is opportunistic
because the transport turns 403/429/451 into ChallengeExpiredError before a body
is readable, which is the normal state of a protected page.

The sec-cpt layer (HTTP 428, PoW or behavioural challenge) is not implemented:
the solver reports it with the markers it saw instead of retrying into a wall.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ProxyError, UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase

from . import abck, wire
from .endpoints import (
    ABCK_COOKIE,
    MAX_SENSOR_POSTS,
    SEC_CPT_COOKIE,
    STATUS_PRECONDITION,
)


def _encoder(payload: dict[str, Any]) -> str:
    """Which encoder the builder was asked for (payload mirror of its extra knob)."""
    return str(payload.get("sensor_encoder") or "js").lower()


class AkamaiSensorSolver(AntibotBase):
    """Classic Akamai sensor_data POST."""

    engine = AntibotEngine.AKAMAI

    info: ClassVar[SolverInfo] = SolverInfo(
        name="akamai_sensor",
        types=(CaptchaType.AKAMAI_SENSOR,),
        http=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        self.require_ctx(ctx, "http", "fingerprint_store")
        payload = self.payload_or_fail(task)
        jar = wire.cookies_of(payload.get("cookies"))
        url = wire.page_url(task, payload)
        ua = wire.user_agent(task, ctx)

        boot = await wire.bootstrap(ctx, url, jar, ua)
        jar.update(boot.cookies)
        state = abck.read(jar.get(ABCK_COOKIE))
        if state.satisfied:
            return self._result(jar, ua, started=started)

        # The model encoder does not need bmak.js; the JS encoder always does.
        script_url: str | None = None
        script: str | None = None
        if _encoder(payload) != "model":
            script_url, script = await self._script(ctx, payload, boot, ua, jar)
        builder, build_ctx = self.build_sensor(
            ctx,
            {**payload, "challenge_js": script, "site_url": url},
            cookies=jar,
            headers={"User-Agent": ua},
            timestamp_ms=wire.now_ms(),
            extra={"js_runtime": ctx.js_runtime, "sensor_encoder": _encoder(payload)},
        )
        sensor_data = await builder.build(build_ctx)

        # The endpoint the script chose wins over every guess: it is the only
        # per-tenant value that came from the live code rather than from us.
        target = wire.first_str(
            payload.get("akam_url"),
            payload.get("endpoint"),
            build_ctx.extra.get("sensor_target"),
            script_url,
            payload.get("sensor_js_url"),
            boot.url,
        )
        if target is None:  # pragma: no cover - boot.url is always set
            raise UnsolvableError("akamai: no sensor endpoint discovered", context={"url": url})

        body, body_headers = wire.sensor_body(sensor_data, form=wire.wants_form(payload))
        headers = {"User-Agent": ua, "Referer": url, "Origin": wire.origin_of(url), **body_headers}

        for posts in range(1, MAX_SENSOR_POSTS + 1):
            resp = await self._post(ctx, target, body, headers=headers, jar=jar, posts=posts)
            jar.update(wire.merge_set_cookie(resp.headers_all("set-cookie")))
            state = abck.read(jar.get(ABCK_COOKIE))
            if state.satisfied:
                return self._result(jar, ua, started=started)
            if resp.status == STATUS_PRECONDITION:
                raise self._sec_cpt(jar, url, posts)

        raise ChallengeExpiredError(
            "akamai: sensor_data posted but _abck never settled",
            context={**state.summary(), "posts": MAX_SENSOR_POSTS, "url_len": len(url)},
        )

    # --- steps ----------------------------------------------------------

    async def _script(
        self,
        ctx: SolverContext,
        payload: dict[str, Any],
        boot: wire.Bootstrap,
        ua: str,
        jar: dict[str, str],
    ) -> tuple[str | None, str]:
        """Resolve the tenant's sensor script: payload first, then the page."""
        inline = wire.first_str(payload.get("sensor_js"), payload.get("challenge_js"))
        if inline:
            return None, inline

        script_url = wire.first_str(payload.get("sensor_js_url"), payload.get("script_url"))
        if script_url is None:
            script_url = wire.sensor_script_url(boot.html, boot.url)
        if script_url is None:
            raise UnsolvableError(
                "akamai: the tenant sensor script is required; send sensor_js (source) or "
                "sensor_js_url, or a websiteURL whose HTML exposes it",
                context={"url_len": len(boot.url), "bootstrap_note": boot.note},
            )
        text = await wire.fetch_text(ctx, script_url, jar, ua, referer=boot.url)
        return script_url, text

    async def _post(
        self,
        ctx: SolverContext,
        url: str,
        body: Any,
        *,
        headers: dict[str, str],
        jar: dict[str, str],
        posts: int,
    ) -> Any:
        try:
            return await self.post_sensor(ctx, url, body, headers=headers, cookies=jar or None)
        except ProxyError:
            raise
        except CaptchaError as exc:
            # The transport hides the body of a 403/429/451, so the only honest
            # reading is "the edge rejected this post".
            raise ChallengeExpiredError(
                "akamai: sensor POST rejected by the edge",
                context={"posts": posts, "reason": exc.code},
            ) from exc

    def _sec_cpt(self, jar: dict[str, str], url: str, posts: int) -> ChallengeExpiredError:
        return ChallengeExpiredError(
            "akamai: sec-cpt challenge (HTTP 428); the PoW/behavioural layer is not implemented",
            context={
                "posts": posts,
                "url_len": len(url),
                "sec_cpt_cleared": abck.sec_cpt_cleared(jar.get(SEC_CPT_COOKIE)),
            },
        )

    def _result(self, jar: dict[str, str], ua: str, *, started: float) -> SolverResult:
        cookies = {name: value for name, value in jar.items() if value}
        state = abck.read(cookies.get(ABCK_COOKIE))
        if not state.satisfied:
            # Only reached through a caller mistake; keep a satisfied cookie from
            # becoming a paid success by accident.
            raise ChallengeExpiredError(
                "akamai: refusing to return an unsatisfied session",
                context=state.summary(),
            )
        return SolverResult(
            token=cookies.get(ABCK_COOKIE),
            extra={"cookies": cookies, "userAgent": ua},
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
