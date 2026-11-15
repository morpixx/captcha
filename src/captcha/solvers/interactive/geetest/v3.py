"""GeeTest v3 (极验三代) — slide puzzle, HTTP only.

Flow:
  1. GET  /get.php?gt=&challenge=...&pt=0     -> round metadata; the round may
     hand back a longer challenge (the widget appends 2 chars) — use that one.
  2. GET  /ajax.php?...&type=slide            -> bg / fullbg / slice paths, c, s
  3. GET  <static>/<bg>, <static>/<slice>     -> puzzle images
  4. CV slider over the pair                  -> horizontal offset
  5. POST /ajax.php with `w`                   -> validate + seccode

The wire constants that were never checked against a live site are FILLs in
`payload.py`; `experimental=True` keeps this solver out of the default pick
until they are captured.
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
from .payload import build_w_v3

_STATIC_KEYS = ("bg", "slice")


@register_solver
class GeetestV3Solver(InteractiveBase):
    info = SolverInfo(
        name="geetest_v3",
        types=(CaptchaType.GEETEST_V3,),
        http=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "gt", "challenge")
        if ctx.http is None:
            raise UnsolvableError("geetest v3 solver requires ctx.http", context={})

        gt = str(task.payload["gt"])
        challenge = str(task.payload["challenge"])
        base = ep.api_base(task.payload.get("api_server"), ep.V3_DEFAULT_API)
        started = time.monotonic()

        # 1. Round metadata. The v3 widget asks get.php for the round type first;
        #    a slide round answers with `type: "slide"`.
        init = await ctx.http.get(
            base + ep.V3_GET,
            params={
                "gt": gt,
                "challenge": challenge,
                "lang": "en",
                "pt": "0",
                "client_type": ep.CLIENT_TYPE,
                "callback": _callback(),
            },
        )
        meta = _geetest_json(init.text)
        if meta.get("status") not in (None, "success"):
            raise ChallengeExpiredError(
                "geetest v3 challenge rejected at get.php",
                context={"status": meta.get("status"), "gt": gt},
            )
        round_data = meta.get("data") if isinstance(meta.get("data"), dict) else {}
        round_challenge = str(round_data.get("challenge") or challenge)

        # 2. Puzzle images.
        # FILL: some builds serve bg/slice from the second `get.php` call and use
        #       `ajax.php` only for the round mode. Open devtools on the target
        #       site, watch which of the two calls returns `bg`/`slice`, and
        #       switch this request accordingly.
        puzzle = await ctx.http.get(
            base + ep.V3_AJAX,
            params={
                "gt": gt,
                "challenge": round_challenge,
                "lang": "en",
                "type": "slide",
                "callback": _callback(),
            },
        )
        pdata = _data(puzzle.text)
        missing = [k for k in _STATIC_KEYS if not pdata.get(k)]
        if missing:
            raise CaptchaError(
                "geetest v3: slide images absent from the challenge response",
                context={"missing": missing, "keys": sorted(pdata)},
            )

        bg = await ctx.http.get(ep.asset_url(ep.V3_STATIC, str(pdata["bg"])))
        piece = await ctx.http.get(ep.asset_url(ep.V3_STATIC, str(pdata["slice"])))

        # 3. Offset from the CV slider primitive.
        # FILL: the v3 bg is a 52-tile shuffle painted by the widget. Restore it
        #       with the tile order array from slide.<ver>.js before handing the
        #       pair to _solve_cv, otherwise the offset is measured on a
        #       scrambled image. `fullbg` from the same response gives the
        #       reference for the restoration.
        cv = await self._solve_cv(CaptchaType.SLIDER, task, ctx, {"bg": bg.body, "piece": piece.body})
        offset = round(float(cv.offset or 0))

        # 4. `w` and the verify round.
        passtime_ms = int((time.monotonic() - started) * 1000.0)
        w = build_w_v3(gt, round_challenge, offset, passtime_ms, secrets.randbits(63))
        verify = await ctx.http.post(
            base + ep.V3_AJAX,
            data={
                "gt": gt,
                "challenge": round_challenge,
                "lang": "en",
                "w": w,
                "callback": _callback(),
            },
        )
        vdata = _data(verify.text)
        validate = vdata.get("validate")
        if not validate:
            raise CaptchaError(
                "geetest v3 verify returned no validate",
                context={"result": vdata.get("result"), "keys": sorted(vdata)},
            )

        return SolverResult(
            token=str(validate),
            extra={
                "challenge": round_challenge,
                "validate": str(validate),
                "seccode": str(vdata.get("seccode") or f"{validate}|jordan"),
            },
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )


def _callback() -> str:
    return f"{ep.CALLBACK_PREFIX}{int(time.time() * 1000)}"


def _geetest_json(text: str) -> dict[str, Any]:
    """Parse a plain or JSONP-wrapped body into a dict."""
    body = text.strip()
    if not body.startswith("{"):
        start, end = body.find("("), body.rfind(")")
        if start != -1 and end > start:
            body = body[start + 1 : end]
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise CaptchaError("geetest v3: response is not JSON", context={"head": text[:160]}) from exc
    if not isinstance(parsed, dict):
        raise CaptchaError("geetest v3: unexpected response shape", context={"type": type(parsed).__name__})
    return parsed


def _data(text: str) -> dict[str, Any]:
    payload = _geetest_json(text)
    inner = payload.get("data")
    return inner if isinstance(inner, dict) else payload
