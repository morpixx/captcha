"""Cloudflare Turnstile sitekey solver.

Turnstile is a sitekey widget — distinct flow from the managed challenge:
no cf_clearance, no interstitial HTML. The widget script loads
``api.js``, then a per-widget payload is POSTed with the behavior track,
returning a ``cf-chl-rc-tk`` / token. We re-implement the POST path.

Both visible and invisible variants hit the same endpoint; the invisible
one just skips the user-interaction gate.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.solvers import SolverContext, SolverInfo

from captcha.solvers.antibot._base import AntibotBase


class TurnstileSolver(AntibotBase):
    """Cloudflare Turnstile — visible + invisible."""

    engine = AntibotEngine.CLOUDFLARE

    info: ClassVar[SolverInfo] = SolverInfo(
        name="turnstile",
        types=(CaptchaType.TURNSTILE, CaptchaType.TURNSTILE_INVISIBLE),
        http=True,
        browser=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors")
        payload = self.payload_or_fail(task)
        # Shape from ingestor:
        #   sitekey (also on task.sitekey), site_url, action (optional),
        #   cdata (optional), chlPageData (dumped from widget), c_ray.
        sitekey = task.sitekey or payload.get("sitekey")
        if not sitekey:
            from captcha.domain.errors import UnsolvableError

            raise UnsolvableError(
                "turnstile requires sitekey",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            extra={
                "sitekey": sitekey,
                "action": payload.get("action"),
                "cdata": payload.get("cdata"),
                "chl_page_data": payload.get("chlPageData"),
            },
        )
        _ = builder, build_ctx
        # FILL: await builder.build(build_ctx) -> {payload bytes, headers}
        # FILL: POST TURNSTILE_WIDGET_FMT.format(variant="g", sitekey=sitekey)
        # FILL: extract token from JSON {"token": "..."} or chunked wire format
        raise self.not_implemented(
            "turnstile",
            next_step="wire widget POST path + token extraction; see runbooks/turnstile.md",
        )
