"""Akamai SBSD (Bot Manager Premier): JSON challenge flow.

SBSD sits in front of the content and answers a JSON POST instead of the classic
sensor form. Its data-generation step is not publicly documented as bytes, so the
solver offers exactly two honest payload sources:

1. the client's own body (payload mode -- it ran the page's script), passed as
   sbsd_body;
2. the engine builder's section model (engines/akamai/builder.py, build_sbsd).

Both are only a *request*. The answer is graded the same way as the classic flow:
the task is solved when _abck settles to the satisfied tail (plus the tenant's
own sbsd cookie when it sets one). A 200 on its own is never a solution, and an
unsettled cookie raises ChallengeExpiredError with the markers that came back.

The endpoint rotates per deploy: sbsd_url from the payload wins, then a path the
challenge HTML names, then the documented /sec/cp_challenge/sbsd family on the
page's own origin.
"""

from __future__ import annotations

import time
from typing import ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.solvers import SolverContext, SolverInfo

from . import abck, wire
from .endpoints import (
    ABCK_COOKIE,
    SBSD_COOKIE,
    SBSD_PATH_HINT,
    SEED_COOKIE,
    STATUS_PRECONDITION,
)
from .solver import AkamaiSensorSolver


class AkamaiSBSDSolver(AkamaiSensorSolver):
    """Akamai Bot Manager Premier (SBSD)."""

    engine = AntibotEngine.AKAMAI

    info: ClassVar[SolverInfo] = SolverInfo(
        name="akamai_sbsd",
        types=(CaptchaType.AKAMAI_SBSD,),
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
        if abck.read(jar.get(ABCK_COOKIE)).satisfied:
            return self._result(jar, ua, started=started)

        target = self._target(payload, boot, url)
        builder, build_ctx = self.build_sensor(
            ctx,
            {**payload, "site_url": url},
            cookies=jar,
            headers={"User-Agent": ua},
            timestamp_ms=wire.now_ms(),
            extra={
                "js_runtime": ctx.js_runtime,
                "sbsd_body": payload.get("sbsd_body"),
                "sbsd_version": payload.get("sbsd_version"),
            },
        )
        build_sbsd = getattr(builder, "build_sbsd", None)
        if not callable(build_sbsd):
            raise ConfigError(
                "akamai_sbsd: the registered akamai builder has no build_sbsd()",
                context={"builder": type(builder).__name__},
            )
        body = build_sbsd(build_ctx)

        resp = await self._post(
            ctx,
            target,
            body,
            headers={
                "User-Agent": ua,
                "Referer": url,
                "Origin": wire.origin_of(url),
                "Content-Type": "application/json",
                "Accept": "*/*",
            },
            jar=jar,
            posts=1,
        )
        jar.update(wire.merge_set_cookie(resp.headers_all("set-cookie")))
        state = abck.read(jar.get(ABCK_COOKIE))
        if resp.status == STATUS_PRECONDITION:
            raise self._sec_cpt(jar, url, 1)
        if not state.satisfied:
            raise ChallengeExpiredError(
                "akamai_sbsd: challenge answered but _abck never settled",
                context={
                    **state.summary(),
                    "status": resp.status,
                    "sbsd_cookie": bool(jar.get(SBSD_COOKIE)),
                    "seed_cookie": bool(jar.get(SEED_COOKIE)),
                },
            )
        return self._result(jar, ua, started=started)

    # --- steps ----------------------------------------------------------

    @staticmethod
    def _target(payload: dict, boot: wire.Bootstrap, url: str) -> str:
        explicit = wire.first_str(payload.get("sbsd_url"), payload.get("endpoint"))
        if explicit:
            return explicit
        discovered = wire.sbsd_url(boot.html, boot.url)
        if discovered:
            return discovered
        if boot.note and not boot.html:
            raise UnsolvableError(
                "akamai_sbsd: the challenge page was not readable and no sbsd_url was sent",
                context={"bootstrap_note": boot.note},
            )
        return wire.origin_of(url) + SBSD_PATH_HINT
