"""Turnstile (invisible) solver.

Cloudflare Turnstile is score-based: the client runs a bundle from
`/cdn-cgi/challenge-platform/h/{b|g}/turnstile/if/ov2/av0/...` that probes the
browser, posts an opaque POST body, and receives `cf_clearance` plus a
Turnstile token. The bundle rotates weekly; no two sitekeys get the same
payload shape.

Live solve path (planned):
  1. Scrape `/cdn-cgi/challenge-platform/h/g/turnstile/if/ov2/av0/<sitekey>`.
  2. Hand the scraped JS + a fingerprint-shaped shim to the JS runtime.
     `captcha.engines.CloudflareChallengeBuilder` already encodes the shim
     surface — reuse it; do not reinvent.
  3. Capture the POST body the bundle tries to send and replay it over
     `ctx.http`.
  4. Return `SolverResult(token=<cf-turnstile-response>)`.

This solver is a skeleton until step 2's runtime wiring lands. See
`runbooks/turnstile.md` for the dump procedure.
"""

from __future__ import annotations

from typing import ClassVar

import structlog

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import CaptchaError, SensorError
from captcha.solvers import Solver, SolverContext, SolverInfo, register_solver

_log = structlog.get_logger(__name__)

# Turnstile endpoints — collected from live observation; keep in one block so a
# patch day only touches this constant set.
CDN_CGI_HOST = "https://challenges.cloudflare.com"
TURNSTILE_IFRAME_FMT = CDN_CGI_HOST + "/cdn-cgi/challenge-platform/h/g/turnstile/if/ov2/av0/{sitekey}"
TURNSTILE_RESULT_FMT = CDN_CGI_HOST + "/cdn-cgi/challenge-platform/h/g/turnstile/jsd/{sitekey}"


@register_solver
class TurnstileInvisibleSolver(Solver):
    """Invisible Turnstile. Visible/managed variants will be a sibling solver."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="turnstile_invisible",
        types=(CaptchaType.TURNSTILE_INVISIBLE,),
        http=True,

        needs_js_runtime=True,
        needs_gpu=False,
        experimental=True,
        version="0.1.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise CaptchaError("turnstile_invisible requires ctx.http", context={"task": str(task.id)})
        if ctx.js_runtime is None:
            raise CaptchaError(
                "turnstile_invisible requires ctx.js_runtime — Turnstile emits obfuscated JS",
                context={"task": str(task.id)},
            )
        if ctx.sensors is None:
            raise CaptchaError(
                "turnstile_invisible requires ctx.sensors (Cloudflare builder)",
                context={"task": str(task.id)},
            )
        if not task.sitekey:
            raise CaptchaError("turnstile payload missing sitekey", context={"task": str(task.id)})
        if not task.site_url:
            raise CaptchaError("turnstile payload missing site_url", context={"task": str(task.id)})

        # The CF sensor builder is ready to accept scraped challenge JS; what it
        # is still missing is a paired JS runtime to actually run it and return
        # the POST body. Until that wiring lands, fail fast with a clear
        # message and surface the inputs the runbook will need to dump.
        try:
            builder = ctx.sensors.get(AntibotEngine.CLOUDFLARE)
        except Exception as exc:
            raise SensorError(
                "cloudflare sensor builder not available",
                context={"engine": AntibotEngine.CLOUDFLARE.value, "err": str(exc)},
            ) from exc

        _log.info(
            "turnstile.solve.skipped",
            reason="live-RE not wired",
            sitekey=task.sitekey,
            site=task.site_url,
            builder=builder.name,
        )

        # FILL: live-RE blockers before this solver can return a real token —
        #   1. fetch TURNSTILE_IFRAME_FMT.format(sitekey=task.sitekey) via ctx.http
        #      with Referer=task.site_url and the user_agent from the fingerprint
        #      store; parse the bootstrap loader URL out of the returned HTML.
        #   2. fetch the bootstrap loader JS and feed it to the CF sensor
        #      builder (ctx.sensors.get(AntibotEngine.CLOUDFLARE)) via a
        #      BuildContext with the scraped JS + a fingerprint pulled from
        #      ctx.fingerprint_store (chrome124 Windows tags).
        #   3. hand the builder's output script to ctx.js_runtime.exec_script
        #      and capture the POST payload it tries to emit to
        #      TURNSTILE_RESULT_FMT; replay the POST over ctx.http.
        #   4. parse `token` out of the response body and return
        #      SolverResult(token=token).
        raise CaptchaError(
            "turnstile_invisible not yet implemented — see runbooks/turnstile.md",
            context={
                "needs": ["cf_challenge_js_scrape", "js_runtime_shim_wiring"],
                "sitekey": task.sitekey,
                "site_url": task.site_url,
            },
        )
