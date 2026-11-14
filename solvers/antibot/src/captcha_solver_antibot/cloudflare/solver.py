"""Cloudflare managed-challenge solver.

Handles the IUAM / "orchestrate/jsch/v1" interstitial: scrapes the
challenge HTML, runs the obfuscated JS in the js-runtime sidecar through
the sensors builder, and returns `cf_clearance`.

Turnstile has a different flow (sitekey widget, browser-free API) and
lives in `turnstile.py` in this subpackage.
"""

from __future__ import annotations

from typing import ClassVar

from captcha_common import SolverResult, Task
from captcha_common.enums import AntibotEngine, CaptchaType
from captcha_solver_base import SolverContext, SolverInfo

from captcha_solver_antibot._base import AntibotBase


class CloudflareChallengeSolver(AntibotBase):
    """Cloudflare managed / IUAM challenge."""

    engine = AntibotEngine.CLOUDFLARE

    info: ClassVar[SolverInfo] = SolverInfo(
        name="cloudflare_challenge",
        types=(CaptchaType.CF_CHALLENGE,),
        http=True,
        browser=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "js_runtime")
        payload = self.payload_or_fail(task)
        # Shape expected from the ingestor:
        #   site_url, challenge_js (raw <script>), cookies (bm_* / __cf_bm),
        #   cvid, ctype, cray, variant, origin.
        # FILL: scrape challenge_js inline instead of requiring it on payload
        #       (worker-side pre-fetch is fine for first cut).
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            cookies=payload.get("cookies"),
            headers=payload.get("headers"),
            extra={"cvid": payload.get("cvid"), "ctype": payload.get("ctype"), "cray": payload.get("cray")},
        )
        _ = builder, build_ctx
        # FILL: await builder.build(build_ctx) -> dict with cf_clearance body
        # FILL: POST to CF_ORCHESTRATE_FMT; parse Set-Cookie for cf_clearance
        # FILL: return SolverResult(token=cf_clearance, extra={"cookies": {...}})
        raise self.not_implemented(
            "cloudflare",
            next_step="wire js-runtime sidecar + orchestrate POST; see runbooks/cloudflare.md",
        )
