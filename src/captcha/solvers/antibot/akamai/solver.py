"""Akamai Bot Manager — classic `sensor_data` solver.

The solver POSTs the base64/urlencoded `sensor_data` blob (built by
`captcha.engines.akamai.builder.AkamaiSensorBuilder`) to the per-tenant akam
endpoint and returns the valid `_abck` cookie. `_abck` versions 2 and
3 are handled by the builder; this solver only transports.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase


class AkamaiSensorSolver(AntibotBase):
    """Classic Akamai `sensor_data` POST."""

    engine = AntibotEngine.AKAMAI

    info: ClassVar[SolverInfo] = SolverInfo(
        name="akamai_sensor",
        types=(CaptchaType.AKAMAI_SENSOR,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "fingerprint_store")
        payload = self.payload_or_fail(task)
        akam_url = payload.get("akam_url") or payload.get("endpoint")
        if not akam_url:
            raise UnsolvableError(
                "akamai requires scraped `akam_url` in payload",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={
                "abck_version": payload.get("abck_version", 3),
                "ua": task.user_agent,
            },
        )
        _ = builder, build_ctx
        # FILL: sensor_body = await builder.build(build_ctx)  # str
        # FILL: resp = await self.post_sensor(ctx, akam_url,
        #           payload={SENSOR_FORM_KEY: sensor_body},
        #           headers={"content-type": "application/x-www-form-urlencoded"})
        # FILL: cookies = self.parse_cookies(resp.headers_all("set-cookie"))
        # FILL: assert _abck is valid (invalid blobs come back with ~0~-N~-1~)
        raise self.not_implemented(
            "akamai",
            next_step="wire sensor_data POST + _abck validation loop; see runbooks/akamai.md",
        )
