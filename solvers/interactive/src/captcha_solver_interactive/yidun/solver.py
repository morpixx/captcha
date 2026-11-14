"""NetEase Yidun (易盾) — slider or 3-point click; returns `validate`.

Flow:
  1. GET  /api/v2/get?id=<captchaId>&fp=<fp>&https=1&type=2    -> challenge
     -> `{result:{bg:[...], front:[...], id, token, ...}}`
  2. GET  <bg>, <front>                                         -> images
  3. Solve CV locally.
  4. POST /api/v2/check with `data` field = base64 of JSON(
        {"id": token, "x": slider_x, "s": track_b64, "data": {"t": ts}})
     -> `{result: true, validate: "<opaque>"}`
"""

from __future__ import annotations

import time
from typing import Any

from captcha_common import CaptchaType, SolverResult, Task
from captcha_common.enums import SolveMode
from captcha_common.errors import CaptchaError, UnsolvableError
from captcha_solver_base.registry import register_solver
from captcha_solver_base.solver import SolverContext, SolverInfo

from .._base import InteractiveBase
from . import endpoints as ep


@register_solver
class YidunSolver(InteractiveBase):
    info = SolverInfo(
        name="yidun",
        types=(CaptchaType.YIDUN,),
        http=True,
        browser=False,
        needs_js_runtime=True,  # `cb` + `fp` are JS-signed
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "captcha_id")
        if ctx.http is None:
            raise UnsolvableError("yidun solver requires ctx.http", context={})

        captcha_id: str = str(task.payload["captcha_id"])
        start = time.monotonic()

        get_params: dict[str, Any] = {
            "id": captcha_id,
            "fp": task.payload.get("fp", ""),  # FILL: `fp` from Yidun's getFp()
            "https": 1,
            "type": 2,  # 2 = slider; 3 = click; 10 = image select
            "version": "2.25.2",
            "dpr": 1,
            "dps": 0,
            "lang": "en",
            "dft": 0,
            "referer": task.site_url or "",
            "callback": "",
        }
        r = await ctx.http.get(ep.GET_CONF, params=get_params)
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("yidun get not JSON", context={"status": r.status}) from exc
        res = data.get("data") or data.get("result") or {}
        if not res:
            raise CaptchaError("yidun get missing result", context={"keys": sorted(data.keys())})

        token = res.get("token") or res.get("id")
        bg_url = _first(res.get("bg"))
        front_url = _first(res.get("front"))
        if not token or not bg_url:
            raise CaptchaError("yidun get missing token/bg", context={"res_keys": sorted(res.keys())})

        bg = await ctx.http.get(bg_url)
        front = await ctx.http.get(front_url) if front_url else None

        cv_kind = CaptchaType.SLIDER if front_url else CaptchaType.CLICK
        cv_payload: dict[str, Any] = {"bg": bg.body}
        if front is not None:
            cv_payload["piece"] = front.body
        cv = await self._solve_cv(cv_kind, task, ctx, cv_payload)

        # Verify payload:
        # The `data` outer is base64 of json that holds `x` (slider end x), `s`
        # (base64 track), and `t` (ms timestamp). The signing key `cb` is a
        # per-session AES. FILL: port cb derivation from the SDK or call sidecar.
        ans_payload = _encode_answer(cv, cv_kind)
        check_body: dict[str, Any] = {
            "id": captcha_id,
            "token": token,
            "data": ans_payload,
            "width": 320,
            # FILL: `cb` — AES-ECB(key=`enc_key`, plaintext=ans_payload)
            "cb": "",
            "extraData": "",
            "runEnv": 10,
            "type": 2 if cv_kind == CaptchaType.SLIDER else 3,
            "version": "2.25.2",
        }
        vr = await ctx.http.post(ep.CHECK, data=check_body)
        try:
            vdata = vr.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("yidun check not JSON", context={"status": vr.status}) from exc
        if not vdata.get("result"):
            raise CaptchaError("yidun check rejected", context={"msg": vdata.get("msg"), "error": vdata.get("error")})
        validate: str | None = vdata.get("validate") or (vdata.get("data") or {}).get("validate")
        if not validate:
            raise CaptchaError(
                "yidun check missing validate — see runbooks/yidun.md",
                context={"vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=validate,
            extra={"validate": validate, "cv_kind": cv_kind.value},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


def _first(x: Any) -> str | None:
    if isinstance(x, list):
        return x[0] if x else None
    if isinstance(x, str):
        return x
    return None


def _encode_answer(cv: SolverResult, kind: CaptchaType) -> str:
    """Produce the plaintext that will then be AES-ECB'd into the `cb` field."""
    import base64
    import json
    if kind == CaptchaType.SLIDER:
        obj = {"x": int(cv.offset or 0), "t": int(time.time() * 1000), "s": ""}
    else:
        obj = {"p": [[int(x), int(y)] for (x, y) in (cv.coords or [])], "t": int(time.time() * 1000)}
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()
