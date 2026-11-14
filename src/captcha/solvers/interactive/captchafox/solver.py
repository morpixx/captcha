"""CaptchaFox — rotate / click challenge, EU-hosted.

Flow (observed widget traffic):
  1. POST /v1/challenge  { siteKey, origin, type:"auto" }       -> challenge
     -> { challengeId, kind: "rotate" | "click", image, piece?, target? }
  2. Solve rotate / click CV.
  3. POST /v1/verify  { challengeId, answer: {angle|coords}, track } -> { token }
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.solvers.registry import register_solver
from captcha.solvers.base import SolverContext, SolverInfo

from .._base import InteractiveBase
from . import endpoints as ep


@register_solver
class CaptchaFoxSolver(InteractiveBase):
    info = SolverInfo(
        name="captchafox",
        types=(CaptchaType.CAPTCHAFOX,),
        http=True,
        browser=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "sitekey")
        if ctx.http is None:
            raise UnsolvableError("captchafox solver requires ctx.http", context={})
        sitekey: str = task.payload["sitekey"]
        origin: str = task.payload.get("origin") or task.site_url or ""
        start = time.monotonic()

        r = await ctx.http.post(
            ep.INIT,
            json={"siteKey": sitekey, "origin": origin, "type": "auto"},
        )
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("captchafox init not JSON", context={"status": r.status}) from exc

        challenge_id = data.get("challengeId") or data.get("id")
        kind = (data.get("kind") or data.get("type") or "").lower()
        img_url = data.get("image") or data.get("bg")
        if not challenge_id or not img_url or kind not in ("rotate", "click"):
            raise CaptchaError(
                "captchafox init payload unexpected — see runbooks/captchafox.md",
                context={"kind": kind, "keys": sorted(data.keys())},
            )

        bg = await ctx.http.get(img_url)
        cv_payload: dict[str, Any] = {"bg": bg.body}
        if kind == "rotate":
            piece_url = data.get("piece")
            if piece_url:
                cv_payload["piece"] = (await ctx.http.get(piece_url)).body
            cv = await self._solve_cv(CaptchaType.ROTATE, task, ctx, cv_payload)
            answer: dict[str, Any] = {"angle": cv.angle or 0.0}
        else:
            cv_payload["target"] = data.get("target", "")
            cv = await self._solve_cv(CaptchaType.CLICK, task, ctx, cv_payload)
            answer = {"coords": [list(p) for p in (cv.coords or [])]}

        verify_body = {
            "challengeId": challenge_id,
            "answer": answer,
            # FILL: `track` — list of pointer samples {t,x,y,e} the SDK records.
            "track": [],
            "siteKey": sitekey,
        }
        vr = await ctx.http.post(ep.VERIFY, json=verify_body)
        try:
            vdata = vr.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("captchafox verify not JSON", context={"status": vr.status}) from exc
        token: str | None = vdata.get("token") or vdata.get("response")
        if not token:
            raise CaptchaError(
                "captchafox verify did not return token — see runbooks/captchafox.md",
                context={"vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=token,
            extra={"kind": kind},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )
