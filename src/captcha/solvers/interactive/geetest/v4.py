"""GeeTest v4 (极验四代) — slide puzzle, HTTP only.

Flow:
  1. GET  /load?captcha_id=&challenge=...      -> lot_number, pow_detail, bg/slice
  2. GET  <static>/<bg>, <static>/<slice>      -> puzzle images
  3. CV slider over the pair                   -> horizontal offset
  4. PoW + `w`                                  -> proof-of-work, AES+RSA envelope
  5. GET  /verify?...&w=                       -> seccode / captcha_output

The v4 answer the client hands back to the site owner is the verify response:
`captcha_output`, `pass_token`, `gen_time`, `lot_number`, `captcha_id` —
which is exactly the five-key solution shape in `domain/solution.py`.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep
from .payload import build_w_v4, solve_pow


@register_solver
class GeetestV4Solver(InteractiveBase):
    info = SolverInfo(
        name="geetest_v4",
        types=(CaptchaType.GEETEST_V4,),
        http=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "captcha_id")
        if ctx.http is None:
            raise UnsolvableError("geetest v4 solver requires ctx.http", context={})

        captcha_id = str(task.payload["captcha_id"])
        challenge = str(task.payload.get("challenge") or "")
        base = ep.api_base(task.payload.get("api_server"), ep.V4_DEFAULT_API)
        started = time.monotonic()

        # 1. Round: lot number, PoW parameters and image paths.
        load = await ctx.http.get(
            base + ep.V4_LOAD,
            params={
                "captcha_id": captcha_id,
                "challenge": challenge,
                "client_type": ep.CLIENT_TYPE,
                "lang": task.language or "eng",
                "callback": _callback(),
            },
        )
        data = _data(load.text)
        lot_number = str(data.get("lot_number") or "")
        if not lot_number:
            raise ChallengeExpiredError(
                "geetest v4 load returned no lot_number",
                context={"captcha_id": captcha_id, "keys": sorted(data)},
            )
        if not data.get("bg") or not data.get("slice"):
            raise CaptchaError(
                "geetest v4: slide images absent from the load response",
                context={"keys": sorted(data), "captcha_type": data.get("captcha_type")},
            )

        bg = await ctx.http.get(ep.asset_url(ep.V4_STATIC, str(data["bg"])))
        piece = await ctx.http.get(ep.asset_url(ep.V4_STATIC, str(data["slice"])))
        cv = await self._solve_cv(CaptchaType.SLIDER, task, ctx, {"bg": bg.body, "piece": piece.body})
        offset = round(float(cv.offset or 0))

        # 2. Proof of work from `pow_detail`, then the `w` envelope.
        pow_msg, pow_sign = solve_pow(lot_number, captcha_id, data.get("pow_detail") or {}, seed=offset)
        passtime_ms = int((time.monotonic() - started) * 1000.0)
        w = build_w_v4(lot_number, captcha_id, offset, passtime_ms, secrets.randbits(63), f"{pow_msg}|{pow_sign}")

        # 3. Verify. `payload`/`process_token` come from the load response.
        # FILL: the current build answers `/verify` on a GET with query params
        #       (payload, process_token, payload_protocol, pt). If the target site
        #       posts a form instead, capture that request in devtools and switch
        #       this call to ctx.http.post with the same fields.
        verify = await ctx.http.get(
            base + ep.V4_VERIFY,
            params={
                "callback": _callback(),
                "captcha_id": captcha_id,
                "client_type": ep.CLIENT_TYPE,
                "lot_number": lot_number,
                "payload": str(data.get("payload") or ""),
                "process_token": str(data.get("process_token") or ""),
                "payload_protocol": "1",
                "pt": str(data.get("pt") or "1"),
                "w": w,
            },
        )
        result = _data(verify.text)
        captcha_output = str(result.get("captcha_output") or result.get("seccode") or "")
        if not captcha_output:
            raise CaptchaError(
                "geetest v4 verify returned no captcha_output/seccode",
                context={"result": result.get("result"), "keys": sorted(result)},
            )

        # FILL: older deployments answer with captcha_output/pass_token/gen_time;
        #       the newest one (`gcaptcha4.geevisit.com`) answers with `seccode`
        #       only. Capture one successful verify response and map the five
        #       solution keys from what it actually returns.
        pass_token = str(result.get("pass_token") or result.get("seccode") or captcha_output)
        gen_time = str(result.get("gen_time") or int(time.time() * 1000))

        return SolverResult(
            token=captcha_output,
            extra={
                "captcha_id": captcha_id,
                "lot_number": lot_number,
                "pass_token": pass_token,
                "gen_time": gen_time,
                "captcha_output": captcha_output,
            },
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )


def _callback() -> str:
    return f"{ep.CALLBACK_PREFIX}{int(time.time() * 1000)}"


def _data(text: str) -> dict[str, Any]:
    """Parse a plain or JSONP-wrapped body, then unwrap its `data` object."""
    body = text.strip()
    if not body.startswith("{"):
        start, end = body.find("("), body.rfind(")")
        if start != -1 and end > start:
            body = body[start + 1 : end]
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise CaptchaError("geetest v4: response is not JSON", context={"head": text[:160]}) from exc
    if not isinstance(parsed, dict):
        raise CaptchaError("geetest v4: unexpected response shape", context={"type": type(parsed).__name__})
    inner = parsed.get("data")
    return inner if isinstance(inner, dict) else parsed
