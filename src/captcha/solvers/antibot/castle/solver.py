"""Castle.io solver.

Castle is simpler than the stack-WAFs: one POST with device signals
yields a client token the app sends as `X-Castle-Client-Id`.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase


class CastleSolver(AntibotBase):
    """Castle.io device-signals token."""

    engine = AntibotEngine.CASTLE

    info: ClassVar[SolverInfo] = SolverInfo(
        name="castle",
        types=(CaptchaType.CASTLE,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "fingerprint_store")
        payload = self.payload_or_fail(task)
        pk = payload.get("publishable_key") or payload.get("pk")
        if not pk:
            raise UnsolvableError(
                "castle requires `publishable_key` in payload",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            headers=payload.get("headers"),
            extra={"publishable_key": pk, "ua": task.user_agent},
        )
        _ = builder, build_ctx
        # FILL: body = await builder.build(build_ctx) -> dict
        # FILL: resp = await self.post_sensor(ctx, CASTLE_COLLECT_URL, body,
        #           headers={"content-type": "application/json",
        #                    "x-castle-publishable-key": pk})
        # FILL: token = resp.json()["token"]; return SolverResult(token=token)
        raise self.not_implemented(
            "castle",
            next_step="wire /v1/c/mon POST + token extraction; see runbooks/castle.md",
        )
