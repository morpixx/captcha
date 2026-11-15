"""NetEase Yidun (易盾) — slider or 3-point click; returns `validate`.

Flow:
  1. GET  /api/v2/get?id=<captchaId>&fp=&type=2                  -> challenge
     -> token, bg/front URLs, zoneId, type
  2. GET  <bg>, <front>                                          -> images
  3. CV slider / click in-process
  4. seal the answer into the `data`+`cb` envelope
  5. POST /api/v2/check  {id, token, data, cb, runEnv}            -> validate

Step 4 is the vendor's SDK: `data` is the AES-sealed pointer track and `cb` is
an environment tag, both produced by JS that inspects the DOM, canvas and mouse
history. The solver does not reimplement that cipher; it takes a sealer from
`ctx.extra["yidun_signer"]` and escalates with NeedsBrowserError otherwise,
because an unsealed `check` gets a rejection that looks like a wrong answer.
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
class YidunSolver(InteractiveBase):
    info = SolverInfo(
        name="yidun",
        types=(CaptchaType.YIDUN,),
        http=True,
        browser=True,
        needs_js_runtime=True,  # `data`/`cb` are SDK-sealed
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "captcha_id")
        if ctx.http is None:
            raise ConfigError("yidun solver requires ctx.http", context={"type": task.type.value})

        captcha_id = str(task.payload["captcha_id"])
        started = time.monotonic()

        reply = await ctx.http.get(ep.GET_CONF, params=self._get_params(task, captcha_id))
        challenge = wire.parse_challenge(wire.parse_reply(reply.text))

        bg = await ctx.http.get(challenge["bg"])
        front = await ctx.http.get(challenge["front"]) if challenge["front"] else None
        cv_payload: dict[str, Any] = {"bg": bg.body}
        if front is not None:
            cv_payload["piece"] = front.body
        cv = await self._solve_cv(challenge["kind"], task, ctx, cv_payload)

        plain = wire.answer_plaintext(cv, challenge["kind"], started=started)
        sealed = await _seal(ctx, {"plain": plain, "token": challenge["token"], "type": challenge["type"]})
        body = wire.check_body(challenge, sealed, captcha_id=captcha_id)
        checked = wire.parse_reply((await ctx.http.post(ep.CHECK, data=body)).text)
        validate = wire.parse_check(checked)

        return SolverResult(
            token=validate,
            extra={"validate": validate, "cv_kind": challenge["kind"].value},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        return await browser.solve(task, ctx, solver=self)

    def _get_params(self, task: Task, captcha_id: str) -> dict[str, Any]:
        """Query the SDK sends for the challenge; `fp` is its device hint."""
        return {
            "id": captcha_id,
            "fp": str(task.payload.get("fp") or ""),
            "https": 1,
            "type": int(task.payload.get("ctype") or ep.TYPE_SLIDER),
            "version": str(task.payload.get("version") or "2.25.2"),
            "dpr": 1,
            "dps": 0,
            "lang": str(task.language or task.payload.get("lang") or "en"),
            "dft": 0,
            "referer": str(task.site_url or task.payload.get("pageurl") or ""),
        }


async def _seal(ctx: SolverContext, envelope: dict[str, Any]) -> dict[str, Any]:
    """Seal the answer with the wired SDK signer, or ask for the browser lane.

    The signer is an async callable (or an object with `seal()`) receiving
    `{plain, token, type}` and returning `{"data", "cb"}`. Nothing here guesses
    the cipher: a wrong `cb` burns the round and reports a false rejection.
    """
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    signer = extra.get(ep.SEALER_KEY)
    if signer is None:
        raise NeedsBrowserError(
            "yidun: data/cb sealing needs the vendor SDK in a browser",
            context={
                "artifact": "cb",
                "hint": f"wire ctx.extra['{ep.SEALER_KEY}'] or route the task to the browser lane",
            },
        )
    call = getattr(signer, "seal", signer)
    sealed = call(envelope)
    if inspect.isawaitable(sealed):
        sealed = await sealed
    if not isinstance(sealed, dict):
        raise UnsolvableError(
            "yidun sealer returned a non-object envelope",
            context={"type": type(sealed).__name__},
        )
    return sealed
