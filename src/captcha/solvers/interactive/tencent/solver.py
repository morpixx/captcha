"""Tencent TCaptcha / 防水墙 — slider or 3-point click.

Flow:
  1. GET  /cap_union_prehandle?aid=<aid>&...                 -> prehandle JSON
     Pulls `sess`, `tdc_path`, `cdnPic1`, `cdnPic2`, puzzle layout, challenge type.
  2. GET  <cdnPic1> + <cdnPic2>                              -> bg + piece (or marked bg)
  3. Solve CV locally. Build `collect` (TDC-Device-Context) + mouse track.
  4. POST /cap_union_new_verify                              -> ticket + randstr
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
class TencentSolver(InteractiveBase):
    info = SolverInfo(
        name="tencent",
        types=(CaptchaType.TENCENT,),
        http=True,

        needs_js_runtime=True,  # TDC collect() is obfuscated JS
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "aid")
        if ctx.http is None:
            raise UnsolvableError("tencent solver requires ctx.http", context={})

        aid: str = str(task.payload["aid"])
        site_url: str = task.payload.get("site_url") or task.site_url or ""
        start = time.monotonic()

        prehandle_params: dict[str, Any] = {
            "aid": aid,
            "accver": 1,
            "showtype": "popup",
            "ua": task.payload.get("ua_b64") or "",  # base64(user-agent)
            "noheader": 1,
            "fb": 1,
            "aged": 0,
            "enableAged": 0,
            "enableDarkMode": 0,
            "grayscale": 1,
            "clientype": 2,
            "cap_cd": "",
            "uid": "",
            "lang": "en",
            "entry_url": site_url,
            # FILL: `subsid`, `callback` (JSONP name), `sess`, `sid` per call.
        }
        r = await ctx.http.get(ep.PREHANDLE, params=prehandle_params)
        payload = _strip_jsonp(r.text)
        try:
            data = _json_loads(payload)
        except Exception as exc:
            raise CaptchaError("tencent prehandle not decodable", context={"head": payload[:160]}) from exc

        sess = data.get("sess") or data.get("sid")
        pic1 = data.get("cdnPic1")
        pic2 = data.get("cdnPic2")
        if not sess or not pic1:
            raise CaptchaError("tencent prehandle missing sess/cdnPic1", context={"keys": sorted(data.keys())})

        # Images: pic1 is bg with slot; pic2 is piece (slider) OR None for click.
        bg = await ctx.http.get(pic1)
        piece = await ctx.http.get(pic2) if pic2 else None

        cv_kind = CaptchaType.SLIDER if pic2 else CaptchaType.CLICK
        cv_payload: dict[str, Any] = {"bg": bg.body}
        if piece is not None:
            cv_payload["piece"] = piece.body
        cv = await self._solve_cv(cv_kind, task, ctx, cv_payload)

        # Verify body — collect is TDC (tdc_v3.js) device fingerprint.
        # FILL: run `window.TDC.getData(true)` under ctx.js_runtime or port
        #       the collect() to Python if the current build is stable.
        collect_b64 = ""
        verify_body: dict[str, Any] = {
            "aid": aid,
            "protocol": "https",
            "accver": 1,
            "showtype": "popup",
            "ticket": "",
            "sess": sess,
            "ans": _format_ans(cv, cv_kind),
            "pow_answer": "",  # FILL: PoW chain from `pow_prefix` + `pow_difficulty`
            "pow_calc_time": 0,
            "eks": data.get("eks", ""),
            "tlg": data.get("tlg", 0),
            "vsig": data.get("vsig", ""),
            "websig": data.get("websig", ""),
            "subsig": data.get("subsig", ""),
            "collect": collect_b64,
            # FILL: `cdata`, `rnd`, `fpinfo` — all written by the SDK.
        }
        vr = await ctx.http.post(ep.VERIFY_NEW, data=verify_body)
        vpayload = _strip_jsonp(vr.text)
        try:
            vdata = _json_loads(vpayload)
        except Exception as exc:
            raise CaptchaError("tencent verify not decodable", context={"head": vpayload[:160]}) from exc
        if int(vdata.get("errorCode", "1")) != 0:
            raise CaptchaError(
                "tencent verify rejected",
                context={"errorCode": vdata.get("errorCode"), "errorMessage": vdata.get("errorMessage")},
            )
        if not vdata.get("ticket"):
            raise CaptchaError(
                "tencent verify did not implement ticket return path — see runbooks/tencent.md",
                context={"vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=vdata["ticket"],
            text=vdata.get("randstr"),
            extra={"randstr": vdata.get("randstr"), "ticket": vdata["ticket"]},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


def _format_ans(cv: SolverResult, kind: CaptchaType) -> str:
    """Tencent `ans` field encodes target coordinates.

    Slider  : `"<x>,<y>"`          (x is slider offset, y constant per challenge)
    Click 3 : `"x1,y1|x2,y2|x3,y3"` for ordered 3-point text click
    """
    if kind == CaptchaType.SLIDER:
        return f"{int(cv.offset or 0)},5"
    pts = cv.coords or []
    return "|".join(f"{int(x)},{int(y)}" for x, y in pts)


def _strip_jsonp(text: str) -> str:
    """Tencent default-answers JSONP. Strip `cb(` ... `)` when present."""
    s = text.strip()
    if "(" in s and s.endswith(")"):
        return s[s.index("(") + 1 : -1]
    return s


def _json_loads(s: str) -> dict[str, Any]:
    import json

    return json.loads(s)
