"""CutCaptcha — slider puzzle with a per-apikey token.

Flow:
  1. GET  /api/v1/challenge?apikey=<k>&host=<h>     -> { cid, bgUrl, pieceUrl }
  2. Fetch images.
  3. Solve slider.
  4. POST /api/v1/verify  { cid, offset, track }    -> { token }
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
class CutCaptchaSolver(InteractiveBase):
    info = SolverInfo(
        name="cutcaptcha",
        types=(CaptchaType.CUTCAPTCHA,),
        http=True,
        browser=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "apikey")
        if ctx.http is None:
            raise UnsolvableError("cutcaptcha solver requires ctx.http", context={})
        apikey: str = task.payload["apikey"]
        host: str = task.payload.get("host") or task.site_url or ""
        start = time.monotonic()

        r = await ctx.http.get(ep.GET_CHALLENGE, params={"apikey": apikey, "host": host})
        try:
            data = r.json()
        except Exception as exc:
            raise CaptchaError("cutcaptcha challenge not JSON", context={"status": r.status}) from exc
        cid = data.get("cid") or data.get("challengeId")
        bg = data.get("bgUrl") or data.get("bg")
        piece = data.get("pieceUrl") or data.get("piece")
        if not (cid and bg and piece):
            raise CaptchaError(
                "cutcaptcha challenge missing fields — see runbooks/cutcaptcha.md",
                context={"keys": sorted(data.keys())},
            )
        bg_b = await ctx.http.get(bg)
        piece_b = await ctx.http.get(piece)
        cv = await self._solve_cv(
            CaptchaType.SLIDER,
            task,
            ctx,
            {"bg": bg_b.body, "piece": piece_b.body},
        )
        vr = await ctx.http.post(
            ep.VERIFY,
            json={
                "cid": cid,
                "offset": int(cv.offset or 0),
                # FILL: `track` — pointer timeline, vendor rejects straight lines.
                "track": [],
                "apikey": apikey,
            },
        )
        try:
            vdata = vr.json()
        except Exception as exc:
            raise CaptchaError("cutcaptcha verify not JSON", context={"status": vr.status}) from exc
        token = vdata.get("token") or vdata.get("response")
        if not token:
            raise CaptchaError(
                "cutcaptcha verify missing token — see runbooks/cutcaptcha.md",
                context={"vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=token,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )
