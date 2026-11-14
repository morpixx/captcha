"""Binance slider captcha.

Flow:
  1. POST /bapi/composite/v1/public/antibot/getCaptcha  with bnc-uuid + csrftoken
     -> { data: {sig, uid, bgLink, puzzleLink, tag} }
  2. Fetch bgLink + puzzleLink.
  3. Solve slider.
  4. POST /bapi/composite/v1/public/antibot/verify
     Body: { securityCheckResponseValidateId: uid, sig, data: <base64(track+offset)>, ... }
     -> { data: { validateId, token } }
"""

from __future__ import annotations

import time

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep


@register_solver
class BinanceSolver(InteractiveBase):
    info = SolverInfo(
        name="binance",
        types=(CaptchaType.BINANCE,),
        http=True,
        browser=False,
        needs_js_runtime=True,  # `fvideo-id` and `data` payload AES-key are JS-signed
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise UnsolvableError("binance solver requires ctx.http", context={})
        self._require(task.payload, "bizScene")
        start = time.monotonic()

        headers: dict[str, str] = {
            "bnc-uuid": task.payload.get("bnc_uuid", ""),
            "csrftoken": task.payload.get("csrftoken", ""),
            "fvideo-id": task.payload.get("fvideo_id", ""),  # FILL: generate
            "x-trace-id": task.payload.get("trace_id", ""),
            "x-ui-request-trace": task.payload.get("trace_id", ""),
            "clienttype": "web",
            "content-type": "application/json",
        }
        r = await ctx.http.post(
            ep.GET_CAPTCHA,
            json={"bizScene": task.payload["bizScene"], "securityCheckResponseValidateId": ""},
            headers=headers,
        )
        try:
            data = (r.json() or {}).get("data") or {}
        except Exception as exc:
            raise CaptchaError("binance getCaptcha not JSON", context={"status": r.status}) from exc

        sig = data.get("sig")
        uid = data.get("uid") or data.get("validateId")
        bg = data.get("bgLink") or data.get("bg")
        piece = data.get("puzzleLink") or data.get("puzzle")
        if not (sig and uid and bg and piece):
            raise CaptchaError(
                "binance getCaptcha missing fields — see runbooks/binance.md",
                context={"keys": sorted(data.keys())},
            )

        bg_bytes = await ctx.http.get(bg)
        piece_bytes = await ctx.http.get(piece)
        cv = await self._solve_cv(
            CaptchaType.SLIDER,
            task,
            ctx,
            {"bg": bg_bytes.body, "piece": piece_bytes.body},
        )

        # Encode answer: binance expects AES-CBC(key=sig[:16], iv=sig[16:32]) of
        # json({offset, track:[(t,x,y)], ...}). FILL: implement in-session key
        # expansion from sig.
        encoded = _encode_answer_stub(sig, cv.offset or 0.0)
        vr = await ctx.http.post(
            ep.VERIFY,
            json={
                "securityCheckResponseValidateId": uid,
                "sig": sig,
                "data": encoded,
                "bizScene": task.payload["bizScene"],
            },
            headers=headers,
        )
        try:
            vdata = (vr.json() or {}).get("data") or {}
        except Exception as exc:
            raise CaptchaError("binance verify not JSON", context={"status": vr.status}) from exc
        token = vdata.get("token") or vdata.get("validateId")
        if not token:
            raise CaptchaError(
                "binance verify missing token — see runbooks/binance.md",
                context={"vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=token,
            extra={"validateId": uid},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


def _encode_answer_stub(sig: str, offset: float) -> str:
    """FILL: AES-CBC encode of the track blob.

    Keep returning "" until the real routine is wired — the verify call will
    then fail with a diagnosable 429 instead of a silent accept.
    """
    _ = (sig, offset)
    return ""
