"""Basilisk — click the sponsor logo in a grid.

Flow:
  1. POST /v1/challenge { siteKey, origin }        -> {challengeId, image, target_text}
  2. Fetch `image` (one PNG with the full grid).
  3. CLICK CV returns coords.
  4. POST /v1/verify { challengeId, coords }       -> { token }
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
class BasiliskSolver(InteractiveBase):
    info = SolverInfo(
        name="basilisk",
        types=(CaptchaType.BASILISK,),
        http=True,
        browser=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "sitekey")
        if ctx.http is None:
            raise UnsolvableError("basilisk solver requires ctx.http", context={})
        sitekey: str = task.payload["sitekey"]
        origin: str = task.payload.get("origin") or task.site_url or ""
        start = time.monotonic()

        r = await ctx.http.post(ep.CHALLENGE, json={"siteKey": sitekey, "origin": origin})
        try:
            data = r.json()
        except Exception as exc:
            raise CaptchaError("basilisk challenge not JSON", context={"status": r.status}) from exc
        cid = data.get("challengeId")
        img_url = data.get("image")
        target = data.get("target") or data.get("prompt")
        if not (cid and img_url and target):
            raise CaptchaError(
                "basilisk challenge missing fields — see runbooks/basilisk.md",
                context={"keys": sorted(data.keys())},
            )
        img = await ctx.http.get(img_url)
        cv = await self._solve_cv(CaptchaType.CLICK, task, ctx, {"bg": img.body, "target": target})
        vr = await ctx.http.post(
            ep.VERIFY,
            json={
                "challengeId": cid,
                "coords": [list(p) for p in (cv.coords or [])],
                # FILL: `track` (pointer samples), `dwell` (ms before first click)
                "track": [],
            },
        )
        try:
            vdata = vr.json()
        except Exception as exc:
            raise CaptchaError("basilisk verify not JSON", context={"status": vr.status}) from exc
        token = vdata.get("token") or vdata.get("response")
        if not token:
            raise CaptchaError(
                "basilisk verify missing token — see runbooks/basilisk.md",
                context={"vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=token,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )
