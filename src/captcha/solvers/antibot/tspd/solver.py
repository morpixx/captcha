"""F5 Shape (TSPD) solver.

Shape is the heaviest of the stack-WAFs: polymorphic obfuscation, VM
bytecode, DOM-event sampling. The builder runs the challenge in the
js-runtime sandbox and produces a cookie body; this solver transports it
to the per-tenant POST path.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase


class TSPDSolver(AntibotBase):
    """F5 Shape (TSPD integrity cookie)."""

    engine = AntibotEngine.TSPD

    info: ClassVar[SolverInfo] = SolverInfo(
        name="tspd",
        types=(CaptchaType.TSPD,),
        http=True,

        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "js_runtime", "fingerprint_store")
        payload = self.payload_or_fail(task)
        tenant_post_url = payload.get("tspd_url") or payload.get("endpoint")
        if not tenant_post_url:
            raise UnsolvableError(
                "tspd requires scraped `tspd_url` in payload",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={
                "script_src": payload.get("script_src"),
                "vm_bytecode": payload.get("vm_bytecode"),
                "ua": task.user_agent,
            },
        )
        _ = builder, build_ctx
        # FILL: body = await builder.build(build_ctx)
        # FILL: resp = await self.post_sensor(ctx, tenant_post_url, body)
        # FILL: ts_cookie = next cookie whose name starts with 'TS' and is 16+ hex
        raise self.not_implemented(
            "tspd",
            next_step="port Shape VM to js-runtime; grind polymorphic bytecode; see runbooks/tspd.md",
        )
