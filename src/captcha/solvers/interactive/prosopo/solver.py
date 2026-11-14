"""Prosopo Procaptcha — image grid click, commitments signed via SR25519.

Flow:
  1. GET  /v1/prosopo/provider/client/captcha/image?user=<addr>&dapp=<addr>&datasetId=<ds>
     -> { captchas: [ {captchaId, items:[{hash,data?,type}], target, salt} ], ... }
  2. For each captcha in the challenge, pick the grid indices whose items match `target`.
  3. POST /v1/prosopo/provider/client/solution
     Body: { captchas: [{captchaId, solution:[idx,...], salt, timestamp}], userSignature }
  4. Reply -> { status: "ok", commitmentId } — the dapp reads commitmentId and queries
     the provider for a boolean verdict; the "token" returned to the integrator is
     the commitmentId.
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
class ProsopoSolver(InteractiveBase):
    info = SolverInfo(
        name="prosopo",
        types=(CaptchaType.PROSOPO,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "user", "dapp")
        if ctx.http is None:
            raise UnsolvableError("prosopo solver requires ctx.http", context={})
        user: str = task.payload["user"]
        dapp: str = task.payload["dapp"]
        dataset_id: str = task.payload.get("datasetId", "")
        start = time.monotonic()

        r = await ctx.http.get(
            ep.GET_CHALLENGE,
            params={"user": user, "dapp": dapp, "datasetId": dataset_id},
        )
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("prosopo challenge not JSON", context={"status": r.status}) from exc

        captchas = data.get("captchas") or []
        if not captchas:
            raise CaptchaError("prosopo returned no captchas", context={"data_keys": sorted(data.keys())})

        solutions: list[dict[str, Any]] = []
        for c in captchas:
            cid = c.get("captchaId")
            target = c.get("target", "")
            items = c.get("items") or []
            # Each item is either an inline b64 image or a URL. Fetch URLs.
            images: list[bytes] = []
            for it in items:
                if it.get("type") == "image":
                    raw = it.get("data")
                    if isinstance(raw, str) and raw.startswith("http"):
                        images.append((await ctx.http.get(raw)).body)
                    elif isinstance(raw, str):
                        import base64
                        images.append(base64.b64decode(raw.split(",", 1)[-1]))
            # Delegate to classification-grid CV. Payload is a list of PNG
            # bytes (row-major) + the natural-language `target`.
            cv = await self._solve_cv(
                CaptchaType.CLASSIFICATION_GRID,
                task,
                ctx,
                {"images": images, "target": target, "n": len(images)},
            )
            solutions.append(
                {
                    "captchaId": cid,
                    "solution": cv.indices or [],
                    "salt": c.get("salt", ""),
                    "timestamp": int(time.time() * 1000),
                }
            )

        # FILL: `userSignature` — detached SR25519 signature over the canonical
        # msgpack-encoded request. See prosopo/captcha/packages/signer.
        body = {
            "user": user,
            "dapp": dapp,
            "captchas": solutions,
            "userSignature": "",
        }
        vr = await ctx.http.post(ep.SUBMIT_IMAGE, json=body)
        try:
            vdata = vr.json()
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("prosopo submit not JSON", context={"status": vr.status}) from exc

        cid: str | None = vdata.get("commitmentId") or vdata.get("commitment_id")
        if not cid:
            raise CaptchaError(
                "prosopo submit missing commitmentId — see runbooks/prosopo.md",
                context={"status": vr.status, "vdata_keys": sorted(vdata.keys())},
            )
        return SolverResult(
            token=cid,
            extra={"commitmentId": cid, "dapp": dapp},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )
