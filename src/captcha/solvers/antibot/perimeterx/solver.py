"""PerimeterX / HUMAN solver.

Builds the `pxcts`/`pxvid`/`_px` payload, POSTs it to the per-tenant
collector, and returns the resulting `_pxhd` cookie. Captcha interstitial
mode (`px3`) hits captcha.px-cloud.net with a different payload — gated
behind the same builder.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase


class PerimeterXSolver(AntibotBase):
    """PerimeterX (HUMAN Security)."""

    engine = AntibotEngine.PERIMETERX

    info: ClassVar[SolverInfo] = SolverInfo(
        name="perimeterx",
        types=(CaptchaType.PERIMETERX,),
        http=True,

        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "js_runtime", "fingerprint_store")
        payload = self.payload_or_fail(task)
        app_id = payload.get("app_id") or payload.get("px_app_id")
        if not app_id:
            raise UnsolvableError(
                "perimeterx requires `app_id` in payload",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={
                "app_id": app_id,
                "vid": payload.get("vid"),
                "sid": payload.get("sid"),
                "uuid": payload.get("uuid"),
                "challenge_type": payload.get("challenge_type", "collector"),
                "ua": task.user_agent,
            },
        )
        _ = builder, build_ctx
        # FILL: body = await builder.build(build_ctx) -> dict
        # FILL: url = COLLECTOR_HOST_FMT.format(app_id=app_id) + COLLECTOR_PATH
        # FILL: resp = await self.post_sensor(ctx, url, body,
        #           headers={"content-type": "application/x-www-form-urlencoded"})
        # FILL: parse response: {do:[{type:"bake","value":"_pxhd=..."}]}
        # FILL: for px3 captcha interstitial, hit CAPTCHA_SUBMIT_FMT instead
        raise self.not_implemented(
            "perimeterx",
            next_step="wire collector POST + `do:bake` parsing; see runbooks/perimeterx.md",
        )
