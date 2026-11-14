"""Hunt — click-the-object captcha with multi-image carousel.

Delegates to CLICK CV; the vendor envelope is per-integrator so URLs come
from the task payload rather than hard-coded.
"""

from __future__ import annotations

import time

from captcha_common import CaptchaType, Task, SolverResult
from captcha_common.enums import SolveMode
from captcha_common.errors import CaptchaError, UnsolvableError
from captcha_solver_base.registry import register_solver
from captcha_solver_base.solver import SolverContext, SolverInfo

from .._base import InteractiveBase
from . import endpoints as ep


@register_solver
class HuntSolver(InteractiveBase):
    info = SolverInfo(
        name="hunt",
        types=(CaptchaType.HUNT,),
        http=True,
        browser=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "host", "session_jwt")
        if ctx.http is None:
            raise UnsolvableError("hunt solver requires ctx.http", context={})
        host: str = task.payload["host"].rstrip("/")
        jwt: str = task.payload["session_jwt"]
        start = time.monotonic()

        headers = {"authorization": f"Bearer {jwt}", "content-type": "application/json"}
        r = await ctx.http.post(host + ep.PATH_INIT, json={}, headers=headers)
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("hunt init not JSON", context={"status": r.status}) from exc

        img_url = data.get("imageUrl") or data.get("image")
        prompt = data.get("prompt") or data.get("target")
        cid = data.get("huntId") or data.get("id")
        if not (img_url and prompt and cid):
            raise CaptchaError(
                "hunt init missing fields — see runbooks/hunt.md",
                context={"keys": sorted(data.keys())},
            )

        img = await ctx.http.get(host + img_url if img_url.startswith("/") else img_url)
        cv = await self._solve_cv(
            CaptchaType.CLICK, task, ctx, {"bg": img.body, "target": prompt}
        )

        vr = await ctx.http.post(
            host + ep.PATH_VERIFY,
            json={
                "huntId": cid,
                "coords": [list(p) for p in (cv.coords or [])],
                # FILL: pointer sample stream — the vendor fingerprints it.
                "track": [],
            },
            headers=headers,
        )
        try:
            vdata = vr.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("hunt verify not JSON", context={"status": vr.status}) from exc
        token = vdata.get("token") or vdata.get("response")
        if not token:
            raise CaptchaError(
                "hunt verify missing token — see runbooks/hunt.md",
                context={"vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=token,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )
