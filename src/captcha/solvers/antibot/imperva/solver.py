"""Imperva / Incapsula solver.

Scrapes the JS interstitial, evaluates it in the js-runtime to compute
the ``___utmvc`` cookie, and POSTs back to `/_Incapsula_Resource`. The
response rotates `incap_ses_*` and sets `visid_incap_*`, which the
caller carries to the real request.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase


class ImpervaSolver(AntibotBase):
    """Imperva (Incapsula)."""

    engine = AntibotEngine.IMPERVA

    info: ClassVar[SolverInfo] = SolverInfo(
        name="imperva",
        types=(CaptchaType.IMPERVA,),
        http=True,

        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "js_runtime")
        payload = self.payload_or_fail(task)
        origin = payload.get("origin") or task.site_url
        if not origin:
            raise UnsolvableError(
                "imperva requires `origin` or task.site_url",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={
                "challenge_src": payload.get("challenge_src"),
                "swssfn": payload.get("swssfn"),
                "utmvc_seed": payload.get("utmvc_seed"),
            },
        )
        _ = builder, build_ctx
        # FILL: utmvc = await builder.build(build_ctx)  # str (URL-encoded)
        # FILL: resp = await self.post_sensor(ctx, origin + INCAPSULA_RESOURCE_PATH,
        #           payload=utmvc, headers={"content-type": "text/plain"})
        # FILL: parse Set-Cookie; return SolverResult(token=<new incap_ses>,
        #           extra={"cookies": self.parse_cookies(resp.headers_all("set-cookie"))})
        raise self.not_implemented(
            "imperva",
            next_step="wire utmvc computation + Incapsula_Resource POST; see runbooks/imperva.md",
        )
