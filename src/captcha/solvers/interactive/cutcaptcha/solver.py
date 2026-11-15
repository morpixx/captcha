"""CutCaptcha — slider puzzle with a per-apikey challenge.

Flow:
  1. GET  /api/v1/challenge?apikey=<k>&host=<h>     -> { cid, bgUrl, pieceUrl }
  2. GET  <bg> + <piece>                            -> images
  3. CV slider in-process                           -> horizontal offset
  4. POST /api/v1/verify  { cid, offset, track }    -> { token }

The answer carries a pointer track, not just the number: the vendor replays it
and rejects a straight line. `wire.drag_track` produces the same eased bezier
the rest of the slider lane uses.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ConfigError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep
from . import wire


@register_solver
class CutCaptchaSolver(InteractiveBase):
    info = SolverInfo(
        name="cutcaptcha",
        types=(CaptchaType.CUTCAPTCHA,),
        http=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "apikey")
        if ctx.http is None:
            raise ConfigError("cutcaptcha solver requires ctx.http", context={"type": task.type.value})

        apikey = str(task.payload["apikey"])
        host = str(task.payload.get("host") or task.site_url or task.payload.get("pageurl") or "")
        started = time.monotonic()

        reply = await ctx.http.get(ep.GET_CHALLENGE, params={"apikey": apikey, "host": host})
        challenge = wire.parse_challenge(wire.parse_reply(reply.text))

        bg = await ctx.http.get(challenge["bg"])
        piece = await ctx.http.get(challenge["piece"])
        cv = await self._solve_cv(CaptchaType.SLIDER, task, ctx, {"bg": bg.body, "piece": piece.body})
        offset = float(cv.offset or 0.0)

        body: dict[str, Any] = {
            "cid": challenge["cid"],
            "offset": round(offset),
            "track": wire.drag_track(offset),
            "apikey": apikey,
        }
        verified = wire.parse_reply((await ctx.http.post(ep.VERIFY, json=body)).text)
        token = wire.parse_verify(verified)

        return SolverResult(
            token=token,
            extra={"offset": round(offset), "cid": challenge["cid"]},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - started) * 1000.0,
        )
