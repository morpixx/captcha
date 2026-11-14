"""Google BotGuard solver.

Pulls the challenge program (``f.txt``), runs it in the sandboxed VM in
js-runtime, and returns the resulting token. Different Google surfaces
(reCAPTCHA, Play Integrity, account sign-in) wrap the same VM with
different I/O — the sensor builder abstracts that; this solver is
mostly transport.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo

from captcha.solvers.antibot._base import AntibotBase


class BotGuardSolver(AntibotBase):
    """Google BotGuard VM."""

    engine = AntibotEngine.BOTGUARD

    info: ClassVar[SolverInfo] = SolverInfo(
        name="botguard",
        types=(CaptchaType.BOTGUARD,),
        http=True,
        browser=False,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "js_runtime", "fingerprint_store")
        payload = self.payload_or_fail(task)
        ftx = payload.get("ftx") or payload.get("program")
        if not ftx:
            raise UnsolvableError(
                "botguard requires scraped `ftx` (f.txt program)",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            headers=payload.get("headers"),
            extra={
                "ftx": ftx,
                "product": payload.get("product", "recaptcha"),
                "callback_url": payload.get("callback_url"),
                "ua": task.user_agent,
            },
        )
        _ = builder, build_ctx
        # FILL: token = await builder.build(build_ctx)  # str (botguard "bgresponse")
        # FILL: if callback_url: POST token; else return directly
        raise self.not_implemented(
            "botguard",
            next_step="port BotGuard VM to js-runtime sandbox; see runbooks/botguard.md",
        )
