"""Kasada KPSDK solver.

Kasada's bootstrap decodes an obfuscated WASM, probes the environment,
and POSTs an encrypted sensor blob to `/fp` / `/tl`. The solver here
threads the sensor builder's output through the right endpoints and
pulls `KP_UIDz` + the `x-kpsdk-*` tokens back out.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase


class KasadaSolver(AntibotBase):
    """Kasada KPSDK."""

    engine = AntibotEngine.KASADA

    info: ClassVar[SolverInfo] = SolverInfo(
        name="kasada",
        types=(CaptchaType.KASADA,),
        http=True,

        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "js_runtime", "fingerprint_store")
        payload = self.payload_or_fail(task)
        origin = payload.get("origin") or task.site_url
        if not origin:
            raise UnsolvableError(
                "kasada requires `origin` or task.site_url",
                context={"task_id": str(task.id)},
            )
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={
                "kpsdk_version": payload.get("kpsdk_version"),
                "kpsdk_v": payload.get("kpsdk_v"),
                "wasm_blob": payload.get("wasm_blob"),
                "ua": task.user_agent,
            },
        )
        _ = builder, build_ctx
        # FILL: ips_resp = await ctx.http.get(origin + KPSDK_PATH_IPS)
        # FILL: body = await builder.build(build_ctx)
        # FILL: fp_resp = await self.post_sensor(ctx, origin + KPSDK_PATH_FP, body,
        #           headers={"x-kpsdk-ct": body["x-kpsdk-ct"], ...})
        # FILL: return token=KP_UIDz, extra={"x-kpsdk-*": fp_resp.header(...)}
        raise self.not_implemented(
            "kasada",
            next_step="port KPSDK WASM to js-runtime, wire /ips->/fp->/tl; see runbooks/kasada.md",
        )
