"""CyberSiARA solver.

Sitekey-widget flow: builder produces the device-fingerprint JSON, we
POST it to `mfpapi.cybersiara.com/api/v1/verify/<sitekey>` and return
the signed token the app's ``MasterController`` expects.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase


class CyberSiARASolver(AntibotBase):
    """CyberSiARA sitekey widget."""

    engine = AntibotEngine.CYBERSIARA

    info: ClassVar[SolverInfo] = SolverInfo(
        name="cybersiara",
        types=(CaptchaType.CYBERSIARA,),
        http=True,

        needs_js_runtime=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "fingerprint_store")
        payload = self.payload_or_fail(task)
        sitekey = task.sitekey or payload.get("sitekey")
        if not sitekey:
            raise UnsolvableError(
                "cybersiara requires sitekey",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            headers=payload.get("headers"),
            extra={"sitekey": sitekey, "ua": task.user_agent},
        )
        _ = builder, build_ctx
        # FILL: body = await builder.build(build_ctx)
        # FILL: resp = await self.post_sensor(ctx,
        #           CYBERSIARA_VERIFY_FMT.format(sitekey=sitekey), body)
        # FILL: token = resp.json()["MasterUrl"] or ["token"]
        raise self.not_implemented(
            "cybersiara",
            next_step="wire verify POST + token extraction; see runbooks/cybersiara.md",
        )
