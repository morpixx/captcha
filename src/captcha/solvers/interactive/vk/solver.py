"""VK — classic image text, or `id.vk.com` slider.

Branches on payload["mode"]:
  - "image"  : GET captcha.php?sid=<sid>, OCR, return key for api.vk.com retry.
  - "slider" : full 3-step dance on id.vk.com.
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
class VKSolver(InteractiveBase):
    info = SolverInfo(
        name="vk",
        types=(CaptchaType.VK,),
        http=True,
        browser=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise UnsolvableError("vk solver requires ctx.http", context={})
        mode = task.payload.get("mode", "image")
        start = time.monotonic()

        if mode == "image":
            self._require(task.payload, "sid")
            sid: str = str(task.payload["sid"])
            img = await ctx.http.get(ep.CAPTCHA_IMAGE, params={"sid": sid})
            cv = await self._solve_cv(
                CaptchaType.IMAGE_TO_TEXT,
                task,
                ctx,
                {"image": img.body, "charset": "latin"},
            )
            if not cv.text:
                raise CaptchaError("vk OCR returned empty text", context={"sid": sid})
            return SolverResult(
                token=cv.text,
                text=cv.text,
                extra={"sid": sid, "mode": "image"},
                solve_mode=SolveMode.HTTP,
                latency_ms=(time.monotonic() - start) * 1000.0,
            )

        if mode == "slider":
            self._require(task.payload, "ckey")
            r = await ctx.http.post(ep.ID_SLIDER_INIT, json={"ckey": task.payload["ckey"]})
            try:
                data = r.json()
            except Exception as exc:
                raise CaptchaError("vk slider init not JSON", context={"status": r.status}) from exc
            bg_url = data.get("bg_url")
            piece_url = data.get("piece_url")
            cid = data.get("cid") or data.get("challenge_id")
            if not (bg_url and piece_url and cid):
                raise CaptchaError(
                    "vk slider init missing fields — see runbooks/vk.md",
                    context={"keys": sorted(data.keys())},
                )
            bg = await ctx.http.get(bg_url)
            piece = await ctx.http.get(piece_url)
            cv = await self._solve_cv(CaptchaType.SLIDER, task, ctx, {"bg": bg.body, "piece": piece.body})
            vr = await ctx.http.post(
                ep.ID_SLIDER_VERIFY,
                json={
                    "cid": cid,
                    "offset": int(cv.offset or 0),
                    # FILL: `track` and `tn` (device token from VK's `frequence.js`)
                    "track": [],
                    "tn": "",
                },
            )
            try:
                vdata = vr.json()
            except Exception as exc:
                raise CaptchaError("vk slider verify not JSON", context={"status": vr.status}) from exc
            token = vdata.get("smart_token") or vdata.get("token")
            if not token:
                raise CaptchaError(
                    "vk slider verify missing token — see runbooks/vk.md",
                    context={"vdata_keys": sorted(vdata.keys())},
                )
            return SolverResult(
                token=token,
                extra={"cid": cid, "mode": "slider"},
                solve_mode=SolveMode.HTTP,
                latency_ms=(time.monotonic() - start) * 1000.0,
            )

        raise UnsolvableError(f"unknown vk mode: {mode}", context={"mode": mode})
