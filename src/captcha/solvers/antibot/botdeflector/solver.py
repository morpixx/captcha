"""BotDeflector solver.

Smaller, less-documented stack — the challenge is a minified probe
script that POSTs a signed JSON to a sibling endpoint and sets the
``bd_token`` cookie. Treated like Castle in shape, like Kasada in
difficulty.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo

from captcha.solvers.antibot._base import AntibotBase


class BotDeflectorSolver(AntibotBase):
    """BotDeflector token."""

    engine = AntibotEngine.BOTDEFLECTOR

    info: ClassVar[SolverInfo] = SolverInfo(
        name="botdeflector",
        types=(CaptchaType.BOTDEFLECTOR,),
        http=True,
        browser=False,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "js_runtime", "fingerprint_store")
        payload = self.payload_or_fail(task)
        collect_url = payload.get("collect_url") or payload.get("endpoint")
        if not collect_url:
            raise UnsolvableError(
                "botdeflector requires scraped `collect_url` in payload",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={
                "script_src": payload.get("script_src"),
                "tenant_key": payload.get("tenant_key"),
                "ua": task.user_agent,
            },
        )
        _ = builder, build_ctx
        # FILL: body = await builder.build(build_ctx)
        # FILL: resp = await self.post_sensor(ctx, collect_url, body)
        # FILL: token = cookies["bd_token"]; return SolverResult(token=token)
        raise self.not_implemented(
            "botdeflector",
            next_step="live RE: capture bootstrap, port signer; see runbooks/botdeflector.md",
        )
