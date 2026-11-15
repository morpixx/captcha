"""atbCaptcha / AiSecurius — slider or 3-point click.

Flow:
  1. GET  /cap-api/v1/get?captchaId=<id>&lang=&type=      -> { data: { token, pic, blockPic } }
  2. GET  <pic> (+ <blockPic>)                            -> images
  3. CV slider / click in-process
  4. seal the answer into `data`
  5. POST /cap-api/v1/check {captchaId, token, data, ts}  -> data.captchaVerification

Step 4 is the vendor's SDK: AES-GCM over the answer under a per-session key the
page derives from `token` and the runtime fingerprint. The solver takes a
sealer from `ctx.extra["atb_sealer"]` and escalates with NeedsBrowserError
otherwise — posting plaintext would get the round rejected as a wrong answer.
"""

from __future__ import annotations

import inspect
import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ConfigError, NeedsBrowserError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import browser, wire
from . import endpoints as ep


@register_solver
class AtbCaptchaSolver(InteractiveBase):
    info = SolverInfo(
        name="atbcaptcha",
        types=(CaptchaType.ATBCAPTCHA,),
        http=True,
        browser=True,
        needs_js_runtime=True,  # the answer is SDK-sealed
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "captcha_id")
        if ctx.http is None:
            raise ConfigError("atbcaptcha solver requires ctx.http", context={"type": task.type.value})

        captcha_id = str(task.payload["captcha_id"])
        type_ = int(task.payload.get("ctype") or ep.TYPE_SLIDER)
        started = time.monotonic()

        reply = await ctx.http.get(ep.GET_CONF, params=self._get_params(task, captcha_id, type_))
        challenge = wire.parse_challenge(wire.parse_reply(reply.text))

        bg = await ctx.http.get(challenge["pic"])
        block = await ctx.http.get(challenge["block"]) if challenge["block"] else None
        cv_payload: dict[str, Any] = {"bg": bg.body}
        if block is not None:
            cv_payload["piece"] = block.body
        cv = await self._solve_cv(challenge["kind"], task, ctx, cv_payload)

        plain = wire.answer_plaintext(cv, challenge["kind"], started=started)
        sealed = await _seal(ctx, {"plain": plain, "token": challenge["token"], "type": type_})
        body = wire.check_body(captcha_id, challenge["token"], sealed, type_=type_)
        checked = wire.parse_reply((await ctx.http.post(ep.VERIFY, json=body)).text)
        verification = wire.parse_check(checked)

        return SolverResult(
            token=verification,
            extra={"captcha_id": captcha_id, "cv_kind": challenge["kind"].value},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        return await browser.solve(task, ctx, solver=self)

    def _get_params(self, task: Task, captcha_id: str, type_: int) -> dict[str, Any]:
        return {
            "captchaId": captcha_id,
            "lang": str(task.language or task.payload.get("lang") or "en"),
            "type": type_,
            "ts": int(time.time() * 1000),
        }


async def _seal(ctx: SolverContext, envelope: dict[str, Any]) -> str:
    """Seal the answer with the wired SDK sealer, or ask for the browser lane.

    The sealer is an async callable (or an object with `seal()`) receiving
    `{plain, token, type}` and returning the `data` string.
    """
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    sealer = extra.get(ep.SEALER_KEY)
    if sealer is None:
        raise NeedsBrowserError(
            "atbcaptcha: the sealed answer needs the vendor SDK in a browser",
            context={
                "artifact": "data",
                "hint": f"wire ctx.extra['{ep.SEALER_KEY}'] or route the task to the browser lane",
            },
        )
    call = getattr(sealer, "seal", sealer)
    sealed = call(envelope)
    if inspect.isawaitable(sealed):
        sealed = await sealed
    if isinstance(sealed, dict):
        sealed = sealed.get("data")
    if not isinstance(sealed, str) or not sealed:
        raise UnsolvableError(
            "atbcaptcha sealer returned an empty data field",
            context={"type": type(sealed).__name__},
        )
    return sealed
