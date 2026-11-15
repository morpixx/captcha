"""GeeTest v3 (极验三代) — slide puzzle, HTTP only.

Flow (the 2025-2026 five-call chain):
  1. register       — site backend returns gt + challenge; already in the task.
  2. GET  /get.php  — round metadata; first 'w' (custom AES half + RSA tail).
  3. GET  /ajax.php — round mode ('slide'); no answer yet.
  4. GET  /get.php  — shuffled bg/fullbg/slice, c/s, challenge + 2 chars.
  5. POST /ajax.php — answer 'w' (custom AES half only) -> validate/seccode.

The first 'w' has the RSA tail, the answer 'w' does not (two 2025 analyses
agree). 'userresponse' is H(offset, challenge34), 'aa' is getAA(track, c, s),
and 'rp' is md5 over either gt+challenge[:32]+passtime or gt+challenge+last t.

Still unverified against a live site: 'ep.v' and the 52-tile background
restoration (the CV slider runs on the shuffled pair; see the runbook FILLs).
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep
from .payload import (
    apply_track_cipher,
    build_w_v3,
    early_passtime,
    encode_track,
    imgload_stand_in,
    track_points,
    v3_params,
    v3_user_response,
)
from .responses import check_status, inner, jsonp, request_headers

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
        lang = task.language or "en"
        risk_type = _risk_type(task.payload)
        headers = request_headers(task)
        started = time.monotonic()

        # 1. Round metadata. The first w carries the RSA tail; the body has no
        #    slider answer yet, so userresponse/aa stay empty on this round.
        first_w = build_w_v3(
            gt,
            challenge,
            "",
            early_passtime(),
            secrets.randbits(63),
            imgload_ms=imgload_stand_in(),
            lang=lang,
            with_rsa=True,
        )
        init = await ctx.http.get(
            base + ep.V3_GET,
            params=v3_params(gt, challenge, lang=lang, risk_type=risk_type, callback=_callback(), w=first_w),
            headers=headers,
        )
        meta = jsonp(init.text, "v3")
        check_status(meta, "v3", "get.php #1", stale_on_fail=True)
        round_data = inner(meta)
        round_challenge = str(round_data.get("challenge") or challenge)

        # 2. Round mode. Some builds serve bg/slice here instead of the second
        #    get.php, so keep the response as a fallback.
        mode = await ctx.http.get(
            base + ep.V3_AJAX,
            params={
                **v3_params(gt, round_challenge, lang=lang, risk_type=risk_type, callback=_callback()),
                "type": "slide",
            },
            headers=headers,
        )
        mode_payload = jsonp(mode.text, "v3")
        check_status(mode_payload, "v3", "ajax.php", stale_on_fail=True)
        mode_data = inner(mode_payload)

        # 3. The second get.php returns the shuffled images, c/s and the
        #    34-char challenge the answer is bound to.
        puzzle = await ctx.http.get(
            base + ep.V3_GET,
            params=v3_params(gt, round_challenge, lang=lang, risk_type=risk_type, callback=_callback()),
            headers=headers,
        )
        puzzle_payload = jsonp(puzzle.text, "v3")
        check_status(puzzle_payload, "v3", "get.php #2", stale_on_fail=True)
        puzzle_data = inner(puzzle_payload)
        if not all(puzzle_data.get(key) for key in _STATIC_KEYS) and all(mode_data.get(key) for key in _STATIC_KEYS):
            puzzle_data = mode_data
        missing = [key for key in _STATIC_KEYS if not puzzle_data.get(key)]
        if missing:
            raise UnsolvableError(
                "geetest v3: slide images absent from the challenge responses",
                context={"missing": missing, "keys": sorted(puzzle_data)},
            )
        challenge = str(puzzle_data.get("challenge") or round_challenge)

        # 4. Images and the CV offset.
        image_started = time.monotonic()
        bg = await ctx.http.get(ep.asset_url(ep.V3_STATIC, str(puzzle_data["bg"])), headers=headers)
        piece = await ctx.http.get(ep.asset_url(ep.V3_STATIC, str(puzzle_data["slice"])), headers=headers)
        imgload_ms = max(1, int((time.monotonic() - image_started) * 1000.0))
        cv = await self._solve_cv(CaptchaType.SLIDER, task, ctx, {"bg": bg.body, "piece": piece.body})
        offset = float(cv.offset or 0.0)

        # 5. Track -> aa -> answer w (no RSA tail).
        points = track_points(offset)
        track = encode_track(points)
        aa = apply_track_cipher(track, puzzle_data.get("c"), str(puzzle_data.get("s") or ""))
        user_response = v3_user_response(offset, challenge)
        passtime_ms = int((time.monotonic() - started) * 1000.0)
        answer_w = build_w_v3(
            gt,
            challenge,
            user_response,
            passtime_ms,
            secrets.randbits(63),
            imgload_ms=imgload_ms,
            aa=aa,
            lang=lang,
            last_t=int(points[-1][2]),
            with_rsa=False,
        )
        verify = await ctx.http.post(
            base + ep.V3_AJAX,
            data={
                "gt": gt,
                "challenge": challenge,
                "lang": lang,
                "w": answer_w,
                "client_type": ep.CLIENT_TYPE,
                "callback": _callback(),
            },
            headers=headers,
        )
        verify_payload = jsonp(verify.text, "v3")
        vdata = inner(verify_payload)
        validate = vdata.get("validate")
        if not validate:
            check_status(verify_payload, "v3", "verify", stale_on_fail=False)
            raise CaptchaError(
                "geetest v3 verify returned no validate",
                context={"result": vdata.get("result"), "keys": sorted(vdata)},
            )

        return SolverResult(
            token=str(validate),
            extra={
                "challenge": challenge,
                "validate": str(validate),
                "seccode": str(vdata.get("seccode") or f"{validate}|jordan"),
            },
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )


def _risk_type(payload: dict[str, Any]) -> str:
    return str(payload.get("risk_type") or payload.get("riskType") or "")


def _callback() -> str:
    return f"{ep.CALLBACK_PREFIX}{int(time.time() * 1000)}"
