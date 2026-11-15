"""hCaptcha HTTP solver: checksiteconfig -> n-token -> getcaptcha.

The wire shapes live in wire.py (ported from the reference client); this module
is the policy on top of them:

  * a pass UUID (P1_ or the bare one passive keys return) is the answer;
  * a passive key answers checksiteconfig with {"pass": true} and no challenge;
    it is solved by getcaptcha with an empty n and no bundle at all;
  * a getcaptcha answer carrying an image challenge is not solvable over HTTP,
    and neither is an answer that rejects the n-token outright - both leave as
    NeedsBrowserError, so the worker moves the envelope to the browser lane
    instead of retrying a fingerprint the site already refused (contract §2:
    one hop, then terminal);
  * protocol breakage (HTTP status, undecodable body, no challenge JWT) stays a
    SensorError and goes through the normal retry policy.

The JS bundle (hsw/hsj) comes from ctx.extra["hcaptcha_js"]: the worker owns the
Node process, the solver never spawns one.
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ConfigError, JSRuntimeError, NeedsBrowserError, SensorError
from captcha.solvers import Solver, SolverContext, SolverInfo

from . import wire

log = logging.getLogger(__name__)


class HCaptchaHTTPSolver(Solver):
    """Pure-HTTP hCaptcha. One challenge per task; no retries inside the solver."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="hcaptcha_http",
        types=(CaptchaType.HCAPTCHA, CaptchaType.HCAPTCHA_ENTERPRISE),
        http=True,
        browser=False,
        needs_js_runtime=True,
        experimental=True,
        version="0.1.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise ConfigError("hcaptcha: solver requires ctx.http", context={"task": str(task.id)})
        runner = _bundle_runner(ctx)
        params = wire.params_from_task(task)
        started = time.monotonic()

        config = await wire.checksite_config(ctx.http, params)
        if config.passive:
            data = await wire.get_captcha_passive(ctx.http, params)
            return _finish(data, params, None, _elapsed(started), passive=True)

        challenge = config.challenge or {}
        req = challenge.get("req")
        if not isinstance(req, str) or not req:
            raise SensorError("hcaptcha: checksiteconfig returned no challenge JWT", context={"host": params.host})

        n_token = await runner.solve(
            req=req,
            host=params.host,
            href=params.href,
            config=params.bundle_config(),
            vm_data=params.vm_data,
        )
        if not isinstance(n_token, str) or not n_token:
            raise JSRuntimeError("hcaptcha: bundle produced an empty n-token", context={"host": params.host})

        data = await wire.get_captcha(ctx.http, params, challenge, config.features, n_token, req, runner)
        return _finish(data, params, challenge, _elapsed(started))


def _elapsed(started: float) -> float:
    return (time.monotonic() - started) * 1000.0


def _finish(
    data: dict[str, Any],
    params: wire.Params,
    challenge: dict[str, Any] | None,
    latency_ms: float,
    *,
    passive: bool = False,
) -> SolverResult:
    token = wire.pass_token(data)
    if token is not None:
        extra: dict[str, Any] = {"userAgent": params.user_agent}
        resp_key = wire.response_key(data, challenge) if challenge is not None else data.get("key")
        if isinstance(resp_key, str) and resp_key:
            extra["respKey"] = resp_key
        log.info(
            "hcaptcha.http.pass sitekey=%s host=%s passive=%s latency_ms=%.1f",
            params.sitekey,
            params.host,
            passive,
            latency_ms,
        )
        return SolverResult(token=token, extra=extra, latency_ms=latency_ms)

    if wire.is_challenge(data):
        raise NeedsBrowserError(
            "hcaptcha: getcaptcha answered with an image challenge",
            context={
                "sitekey": params.sitekey,
                "host": params.host,
                "challenge": wire.challenge_kind(data),
            },
        )
    raise NeedsBrowserError(
        "hcaptcha: getcaptcha rejected the n-token",
        context={
            "sitekey": params.sitekey,
            "host": params.host,
            "error_codes": data.get("error-codes") or [],
            "success": data.get("success"),
            "passive": passive,
        },
    )


def _bundle_runner(ctx: SolverContext) -> wire.BundleRunner:
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    runner = extra.get("hcaptcha_js")
    if runner is None:
        raise ConfigError(
            "hcaptcha: JS bundle runner missing - wire adapters.hcaptcha_js.JsBundleRunner into ctx.extra['hcaptcha_js']"
        )
    return runner
