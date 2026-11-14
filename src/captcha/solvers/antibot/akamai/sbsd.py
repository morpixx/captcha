"""Akamai SBSD (Bot Manager Premier) solver.

SBSD is the newer, JSON-bodied flow — distinct wire format from classic
`sensor_data` even though the backend is the same product. The sensor
builder packs navigator/webdriver/WebGL probes into a JSON envelope and
signs it with an embedded WASM routine; this solver just transports.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo

from captcha.solvers.antibot._base import AntibotBase


class AkamaiSBSDSolver(AntibotBase):
    """Akamai Bot Manager Premier (SBSD)."""

    engine = AntibotEngine.AKAMAI

    info: ClassVar[SolverInfo] = SolverInfo(
        name="akamai_sbsd",
        types=(CaptchaType.AKAMAI_SBSD,),
        http=True,
        browser=False,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "js_runtime", "fingerprint_store")
        payload = self.payload_or_fail(task)
        sbsd_url = payload.get("sbsd_url") or payload.get("endpoint")
        if not sbsd_url:
            raise UnsolvableError(
                "akamai_sbsd requires scraped `sbsd_url` in payload",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={
                "sbsd_version": payload.get("sbsd_version"),
                "wasm_blob": payload.get("wasm_blob"),
                "ua": task.user_agent,
            },
        )
        _ = builder, build_ctx
        # FILL: body = await builder.build(build_ctx)  # dict (JSON)
        # FILL: resp = await self.post_sensor(ctx, sbsd_url, body,
        #           headers={"content-type": "application/json"})
        # FILL: parse Set-Cookie for bm_so / bm_sz; return token=bm_so
        raise self.not_implemented(
            "akamai_sbsd",
            next_step="port SBSD WASM signing into js-runtime sidecar; see runbooks/akamai_sbsd.md",
        )
