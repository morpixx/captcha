"""MTCaptcha — image slider / object-click behind a double-MD5 token envelope.

Flow (observed, MTCaptcha v3):
  1. GET  /mtcv1/api/getchallengev3.json?sk=<sitekey>&...         -> challenge JSON
     Returns: { result: { challenge: { tid, ct, ... }, img: {uri,...}, vd } }
  2. GET  <img.uri>                                               -> PNG bytes
  3. Solve the CV problem locally (slider offset or click points).
  4. POST /mtcv1/api/submitchallenge.json                         -> verifier
     Body: { sk, tid, ct, act: { ... user actions ... } }
     Returns: { result: { verifyResult: { code: 2000, verified: true, vt } } }
  5. Compose the final `v1(...)` token for the website to submit.

FILL points call out the parts that need live traffic capture to confirm:
  - exact query-string params for step 1 (siteWebUrl, u, l, cs, ...)
  - exact `act` shape for step 4 (differs slider vs object-click)
  - two chain hashes (ch1, ch2) derivation (JS reverses to a known MD5 chain)
"""

from __future__ import annotations

import time
from typing import Any

from captcha_common import CaptchaType, SolverResult, Task
from captcha_common.errors import CaptchaError, UnsolvableError
from captcha_solver_base.registry import register_solver
from captcha_solver_base.solver import SolverContext, SolverInfo

from .._base import InteractiveBase
from . import endpoints as ep


@register_solver
class MTCaptchaSolver(InteractiveBase):
    info = SolverInfo(
        name="mtcaptcha",
        types=(CaptchaType.MTCAPTCHA,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "sitekey")
        if ctx.http is None:
            raise UnsolvableError("mtcaptcha solver requires ctx.http", context={})

        sitekey: str = task.payload["sitekey"]
        site_url: str = task.payload.get("site_url") or task.site_url or ""
        start = time.monotonic()

        # 1) fetch challenge
        params: dict[str, Any] = {
            "sk": sitekey,
            "tsms": int(time.time() * 1000),
            "u": site_url,
            # FILL: `l` (language), `cs` (client signature), `js` (client version)
            #       — read values straight off a live network capture of the widget.
        }
        r = await ctx.http.get(ep.GET_CHALLENGE, params=params)
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("mtcaptcha getchallenge returned non-JSON", context={"status": r.status}) from exc

        result = data.get("result") or {}
        challenge = result.get("challenge") or {}
        img = result.get("img") or {}
        if not challenge or not img.get("uri"):
            raise CaptchaError("mtcaptcha init missing challenge/img", context={"result_keys": list(result.keys())})

        tid: str = challenge.get("tid", "")
        ct: str = challenge.get("ct", "")
        img_uri: str = img["uri"]

        # 2) fetch image(s). MTCaptcha ships single or dual images (slider has
        # bg + piece, click has one annotated bg).
        bg = await ctx.http.get(img_uri)
        piece_uri = img.get("piece_uri")
        piece = await ctx.http.get(piece_uri) if piece_uri else None

        # 3) delegate CV
        cv_kind = CaptchaType.SLIDER if piece_uri else CaptchaType.CLICK
        cv_payload: dict[str, Any] = {"bg": bg.body}
        if piece is not None:
            cv_payload["piece"] = piece.body
        cv = await self._solve_cv(cv_kind, task, ctx, cv_payload)

        # 4) assemble action trace + submit
        act = _build_act(cv, cv_kind)
        # FILL: `act` is a JSON object mixing geometry + timing events
        #       recorded by the SDK. The vendor validates jitter and dwell
        #       time; re-capture one real session and mirror its shape.
        submit_body = {
            "sk": sitekey,
            "tid": tid,
            "ct": ct,
            "act": act,
            "sa": "",  # sensorAnswer slot — vendor-specific, FILL from live
        }
        vr = await ctx.http.post(ep.SUBMIT_CHALLENGE, json=submit_body)
        try:
            vdata = vr.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("mtcaptcha submit returned non-JSON", context={"status": vr.status}) from exc

        verify = (vdata.get("result") or {}).get("verifyResult") or {}
        if not verify.get("verified"):
            raise CaptchaError(
                "mtcaptcha verify rejected",
                context={"code": verify.get("code"), "msg": verify.get("msg")},
            )
        vt: str | None = verify.get("vt")
        if not vt:
            raise CaptchaError("mtcaptcha verify had no vt", context={"verify": verify})

        # 5) final token composition. The two chain hashes come out of the JS
        # `_getToken` call which rolls md5 over sk+tid+random salts.
        # FILL: port _getToken from mtcaptcha.min.js into pure Python, or call
        # the sidecar JS runtime (ctx.js_runtime) with the deobbed function.
        raise CaptchaError(
            "mtcaptcha final v1(...) token composition not yet implemented — see runbooks/mtcaptcha.md",
            context={
                "vt": vt,
                "tid": tid,
                "sitekey": sitekey,
                "cv_kind": cv_kind.value,
                "cv_result": {"offset": cv.offset, "coords": cv.coords},
                "latency_ms": (time.monotonic() - start) * 1000.0,
            },
        )


def _build_act(cv: SolverResult, kind: CaptchaType) -> dict[str, Any]:
    """Shape the user-action trace the submitchallenge endpoint expects.

    Vendor validates timing + micro-jitter; use `captcha-sensors` + `captcha-behavior`
    to synthesize a plausible track. FILL on the exact key names (`mv`, `tr`, `up`...).
    """
    if kind == CaptchaType.SLIDER:
        return {
            "type": "slider",
            "dx": cv.offset or 0.0,
            # FILL: `tr` — list of (t,x,y) samples 60Hz; vendor rejects straight lines.
            "tr": [],
        }
    # click
    return {"type": "click", "pts": [list(p) for p in (cv.coords or [])], "tr": []}
