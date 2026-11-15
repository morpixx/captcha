"""GeeTest v4 (极验四代) — slide puzzle, HTTP only.

Flow:
  1. GET  /load?captcha_id=&challenge=<uuid4>&client_type=web&risk_type=&lang=
     -> lot_number, pow_detail, bg/slice, pt, payload, process_token.
  2. GET  <static>/<bg>, <static>/<slice>      -> puzzle images.
  3. CV slider over the pair                   -> horizontal offset.
  4. PoW + w: pt=0 urlencodes the body; pt=1 is AES-128-CBC + RSA.
  5. GET  /verify?callback=&captcha_id=&client_type=&lot_number=&risk_type=
     &payload=&process_token=&payload_protocol=1&pt=&w= -> seccode.

The v4 answer the client hands back to the site owner is the verify response:
captcha_output, pass_token, gen_time, lot_number, captcha_id — exactly the
five-key solution shape in domain/solution.py.

pt=2 (createPoint/initEncipher curve scheme) is public only as a name; the
solver reports it as unsolvable instead of sending a broken w.
"""

from __future__ import annotations

import secrets
import time
from typing import Any
from uuid import uuid4

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep
from .payload import build_w_v4, solve_pow, v4_passtime
from .responses import check_status, inner, jsonp, request_headers


@register_solver
class GeetestV4Solver(InteractiveBase):
    info = SolverInfo(
        name="geetest_v4",
        types=(CaptchaType.GEETEST_V4,),
        http=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        captcha_id = _value(task.payload, "captcha_id", "captchaId")
        if not captcha_id:
            raise UnsolvableError(
                "missing payload fields: ['captcha_id']",
                context={"have": sorted(task.payload)},
            )
        if ctx.http is None:
            raise UnsolvableError("geetest v4 solver requires ctx.http", context={})

        risk_type = _value(task.payload, "risk_type", "riskType") or ep.DEFAULT_RISK_TYPE
        if not risk_type.startswith("slide"):
            raise UnsolvableError(
                "geetest v4: only the slide risk_type is solvable by CV",
                context={"risk_type": risk_type},
            )
        challenge = _value(task.payload, "challenge") or str(uuid4())
        lang = task.language or "eng"
        base = ep.api_base(task.payload.get("api_server"), ep.V4_DEFAULT_API)
        headers = request_headers(task)
        started = time.monotonic()

        # 1. Round: lot number, PoW parameters, image paths, payload/process_token.
        load = await ctx.http.get(
            base + ep.V4_LOAD,
            params={
                "captcha_id": captcha_id,
                "challenge": challenge,
                "client_type": ep.CLIENT_TYPE,
                "risk_type": risk_type,
                "lang": lang,
                "callback": _callback(),
            },
            headers=headers,
        )
        load_payload = jsonp(load.text, "v4")
        check_status(load_payload, "v4", "load", stale_on_fail=True)
        data = inner(load_payload)
        lot_number = str(data.get("lot_number") or "")
        if not lot_number:
            raise ChallengeExpiredError(
                "geetest v4 load returned no lot_number",
                context={"captcha_id": captcha_id, "keys": sorted(data)},
            )
        if not data.get("bg") or not data.get("slice"):
            raise UnsolvableError(
                "geetest v4: slide images absent from the load response",
                context={"keys": sorted(data), "captcha_type": data.get("captcha_type")},
            )
        pt = str(data.get("pt") or _value(task.payload, "pt") or "1")
        payload_field = str(data.get("payload") or _value(task.payload, "payload") or "")
        process_token = str(data.get("process_token") or _value(task.payload, "process_token") or "")

        # 2. Images and the CV offset. Keep the float: userresponse is derived
        #    from it, not from the rounded setLeft in the bundle.
        bg = await ctx.http.get(ep.asset_url(ep.V4_STATIC, str(data["bg"])), headers=headers)
        piece = await ctx.http.get(ep.asset_url(ep.V4_STATIC, str(data["slice"])), headers=headers)
        cv = await self._solve_cv(CaptchaType.SLIDER, task, ctx, {"bg": bg.body, "piece": piece.body})
        left = float(cv.offset or 0.0)

        # 3. Proof of work, then the w envelope selected by pt.
        pow_msg, pow_sign = solve_pow(lot_number, captcha_id, data.get("pow_detail") or {}, seed=int(left))
        passtime_ms = v4_passtime((time.monotonic() - started) * 1000.0)
        w = build_w_v4(
            lot_number,
            captcha_id,
            left,
            passtime_ms,
            secrets.randbits(63),
            f"{pow_msg}|{pow_sign}",
            pt=pt,
            risk_type=risk_type,
        )

        # 4. Verify is a GET in the 2026 build, with payload/process_token and
        #    payload_protocol from the load response.
        verify = await ctx.http.get(
            base + ep.V4_VERIFY,
            params={
                "callback": _callback(),
                "captcha_id": captcha_id,
                "client_type": ep.CLIENT_TYPE,
                "lot_number": lot_number,
                "risk_type": risk_type,
                "payload": payload_field,
                "process_token": process_token,
                "payload_protocol": ep.V4_PAYLOAD_PROTOCOL,
                "pt": pt,
                "w": w,
            },
            headers=headers,
        )
        verify_payload = jsonp(verify.text, "v4")
        check_status(verify_payload, "v4", "verify", stale_on_fail=False)
        result = inner(verify_payload)
        captcha_output = str(result.get("captcha_output") or result.get("seccode") or "")
        if not captcha_output:
            raise CaptchaError(
                "geetest v4 verify returned no captcha_output/seccode",
                context={"result": result.get("result"), "keys": sorted(result)},
            )

        # Newer builds answer with seccode only; older ones carry all five.
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


def _value(payload: dict[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _callback() -> str:
    return f"{ep.CALLBACK_PREFIX}{int(time.time() * 1000)}"
