"""Yandex SmartCaptcha — invisible, checkbox, or 3x3 image grid.

Invisible / checkbox mode: the widget drops a `smart-token` cookie after a
risk score that includes `l10n`, `host`, and UA entropy. Image-grid is
served when the risk score lifts the threshold.

Flow (advanced image-grid mode):
  1. GET  /captcha.js?sitekey=<k>&host=<host>                 -> loader HTML
  2. POST /check  { sitekey, host, challengeId }              -> challenge JSON
     -> { images: [url x9], task: "Find all pictures with X", token_stub }
  3. Solve grid locally (click returns indices).
  4. POST /validate { token_stub, indices: [0,3,5], host, ua } -> { token: "dR001:..." }
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
class YandexSolver(InteractiveBase):
    info = SolverInfo(
        name="yandex",
        types=(CaptchaType.YANDEX,),
        http=True,
        browser=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "sitekey")
        if ctx.http is None:
            raise UnsolvableError("yandex solver requires ctx.http", context={})
        sitekey: str = task.payload["sitekey"]
        host: str = task.payload.get("host") or task.site_url or ""
        start = time.monotonic()

        r = await ctx.http.post(ep.CHECK, json={"sitekey": sitekey, "host": host})
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("yandex /check not JSON", context={"status": r.status}) from exc

        images = data.get("images") or []
        target = data.get("task") or ""
        stub = data.get("token_stub") or data.get("challengeId")
        if not images or not stub:
            raise CaptchaError(
                "yandex /check missing images/token_stub — see runbooks/yandex.md",
                context={"keys": sorted(data.keys())},
            )

        tiles: list[bytes] = []
        for url in images:
            tiles.append((await ctx.http.get(url)).body)

        cv = await self._solve_cv(
            CaptchaType.CLASSIFICATION_GRID,
            task,
            ctx,
            {"images": tiles, "target": target, "n": len(tiles)},
        )

        vr = await ctx.http.post(
            ep.VALIDATE,
            json={
                "token_stub": stub,
                "indices": cv.indices or [],
                "host": host,
                "sitekey": sitekey,
                # FILL: `fp` — SHA256 of (UA + screen + language + accept-encoding)
                "fp": "",
                "ts": int(time.time() * 1000),
            },
        )
        try:
            vdata = vr.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("yandex /validate not JSON", context={"status": vr.status}) from exc
        token = vdata.get("token") or vdata.get("smartToken")
        if not token:
            raise CaptchaError(
                "yandex /validate missing token — see runbooks/yandex.md",
                context={"vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=token,
            extra={"grid_indices": cv.indices or []},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )
