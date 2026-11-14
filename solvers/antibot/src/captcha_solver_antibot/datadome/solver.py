"""DataDome solver.

Covers both the full JS stack (``/js/`` POST that yields the ``datadome``
cookie) and the slider captcha interstitial that appears when the JS
verdict is uncertain. The solver dispatches on `task.type` to the right
payload path and shares endpoint handling for both.
"""

from __future__ import annotations

from typing import ClassVar

from captcha_common import SolverResult, Task
from captcha_common.enums import AntibotEngine, CaptchaType
from captcha_common.errors import UnsolvableError
from captcha_solver_base import SolverContext, SolverInfo

from captcha_solver_antibot._base import AntibotBase


class DataDomeSolver(AntibotBase):
    """DataDome — full and slider variants."""

    engine = AntibotEngine.DATADOME

    info: ClassVar[SolverInfo] = SolverInfo(
        name="datadome",
        types=(CaptchaType.DATADOME_FULL, CaptchaType.DATADOME_SLIDER),
        http=True,
        browser=False,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "fingerprint_store")
        payload = self.payload_or_fail(task)
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={
                "hsh": payload.get("hsh"),
                "cid": payload.get("cid"),
                "s": payload.get("s"),
                "t": payload.get("t"),  # challenge type: fe/pe
                "ua": task.user_agent,
                "flavor": "slider" if task.type is CaptchaType.DATADOME_SLIDER else "full",
            },
        )
        _ = builder, build_ctx

        if task.type is CaptchaType.DATADOME_SLIDER:
            # FILL: pull slider image via DATADOME_SLIDER_IMAGE_FMT, run CV
            #       (captcha_models.slider) to get the gap x-offset,
            #       feed into builder as behavior.slider_offset, then POST
            #       /captcha/check with dd-tc / dd-tr.
            raise self.not_implemented(
                "datadome_slider",
                next_step="wire CV gap detection + /captcha/check POST; see runbooks/datadome.md",
            )

        if task.type is CaptchaType.DATADOME_FULL:
            # FILL: await builder.build(build_ctx) -> dict for /js/ POST
            # FILL: capture Set-Cookie: datadome=...; return token=datadome
            raise self.not_implemented(
                "datadome",
                next_step="wire /js/ POST + datadome cookie extraction; see runbooks/datadome.md",
            )

        raise UnsolvableError(
            f"datadome solver got unexpected type: {task.type.value}",
            context={"type": task.type.value},
        )
