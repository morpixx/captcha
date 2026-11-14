"""Temu / PDD aegis slider — requires `anti_content` fingerprint.

Flow:
  1. POST /api/bg/aegis/captcha/init  with headers `anti-content: <a_c>`
     -> { captchaId, bgUrl, pieceUrl, challenge_type }
  2. Fetch bg + piece.
  3. Solve slider.
  4. POST /api/bg/aegis/captcha/verify with the same `a_c` + track + offset.
"""

from __future__ import annotations

import time

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep


@register_solver
class TemuSolver(InteractiveBase):
    info = SolverInfo(
        name="temu",
        types=(CaptchaType.TEMU,),
        http=True,
        browser=False,
        needs_js_runtime=True,  # anti_content is JS-signed
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise UnsolvableError("temu solver requires ctx.http", context={})

        # anti_content is the whole challenge. It covers UA, canvas hash,
        # audio fp, timing noise, and a counter inside the PDD JS SDK.
        # Without it, both endpoints return 403 / `error_code: 50000`.
        raise CaptchaError(
            "temu solve not yet implemented — see runbooks/temu.md",
            context={
                "needs": [
                    "anti_content (`a_c`) generator ported from PDD aegis bundle",
                    "mouse-track envelope (`track_list`) signing",
                    "slider endpoint schema (FILL from captured burp session)",
                ],
                "endpoints": {"init": ep.VERIFY_INIT, "verify": ep.VERIFY_SUBMIT},
                "started": time.time(),
            },
        )
