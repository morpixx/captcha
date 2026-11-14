"""atbCaptcha / AiSecurius — slider or 3-point click.

Flow:
  1. GET  /cap-api/v1/get?captchaId=<id>&lang=<lang>&type=<type>  -> challenge
     -> { data: { token, pic, blockPic?, workload?, extra } }
  2. Fetch pic(s).
  3. Solve CV.
  4. POST /cap-api/v1/check  Body: { token, data: AES(payload), captchaId }
     -> { success: true, data: { captchaVerification: "<signed>" } }

The site then forwards `captchaVerification` to its own backend.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep


@register_solver
class AtbCaptchaSolver(InteractiveBase):
    info = SolverInfo(
        name="atbcaptcha",
        types=(CaptchaType.ATBCAPTCHA,),
        http=True,
        browser=False,
        needs_js_runtime=True,  # AES key is wrapped by a JS-only routine
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "captcha_id")
        if ctx.http is None:
            raise UnsolvableError("atbcaptcha solver requires ctx.http", context={})
        cid: str = str(task.payload["captcha_id"])
        start = time.monotonic()

        params: dict[str, Any] = {
            "captchaId": cid,
            "lang": task.language or "en",
            # FILL: `type` — numeric per vendor (slider=1, 3-click=2, text=3, rotate=4).
            "type": task.payload.get("ctype", 1),
            "ts": int(time.time() * 1000),
        }
        r = await ctx.http.get(ep.GET_CONF, params=params)
        try:
            data = (r.json() or {}).get("data") or {}
        except Exception as exc:
            raise CaptchaError("atbcaptcha get not JSON", context={"status": r.status}) from exc
        token = data.get("token")
        pic = data.get("pic")
        block = data.get("blockPic")
        if not (token and pic):
            raise CaptchaError(
                "atbcaptcha get missing fields — see runbooks/atbcaptcha.md",
                context={"keys": sorted(data.keys())},
            )
        bg = await ctx.http.get(pic)
        piece_b = await ctx.http.get(block) if block else None
        cv_kind = CaptchaType.SLIDER if block else CaptchaType.CLICK
        cv_payload: dict[str, Any] = {"bg": bg.body}
        if piece_b is not None:
            cv_payload["piece"] = piece_b.body
        cv = await self._solve_cv(cv_kind, task, ctx, cv_payload)

        # FILL: encrypt `payload` with the per-session AES key the SDK derives
        # from `token` + user fingerprint. Current stub sends plaintext so the
        # verify fails with an obvious schema error, not a silent success.
        verify_body = {
            "captchaId": cid,
            "token": token,
            "data": _encode_answer(cv, cv_kind),
            "point": _format_point(cv, cv_kind),
            "ts": int(time.time() * 1000),
        }
        vr = await ctx.http.post(ep.VERIFY, json=verify_body)
        try:
            vdata = vr.json() or {}
        except Exception as exc:
            raise CaptchaError("atbcaptcha check not JSON", context={"status": vr.status}) from exc
        if not vdata.get("success"):
            raise CaptchaError(
                "atbcaptcha check rejected",
                context={"code": vdata.get("code"), "msg": vdata.get("msg")},
            )
        verification = (vdata.get("data") or {}).get("captchaVerification")
        if not verification:
            raise CaptchaError(
                "atbcaptcha check missing captchaVerification — see runbooks/atbcaptcha.md",
                context={"vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=verification,
            extra={"captcha_id": cid, "cv_kind": cv_kind.value},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


def _format_point(cv: SolverResult, kind: CaptchaType) -> str:
    if kind == CaptchaType.SLIDER:
        return f"{int(cv.offset or 0)},5"
    return ",".join(f"{int(x)},{int(y)}" for (x, y) in (cv.coords or []))


def _encode_answer(cv: SolverResult, kind: CaptchaType) -> str:
    """Plaintext stub — vendor expects AES-wrap; FILL that in."""
    import base64
    import json

    if kind == CaptchaType.SLIDER:
        obj = {"x": int(cv.offset or 0), "t": int(time.time() * 1000)}
    else:
        obj = {"pts": [[int(x), int(y)] for (x, y) in (cv.coords or [])]}
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()
