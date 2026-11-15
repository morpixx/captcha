"""Basilisk - ad-funded click captcha.

Flow:
  1. POST /v1/challenge {siteKey, origin} -> challengeId, image, target text.
  2. Fetch the grid image and solve the click through the CV registry.
  3. POST /v1/verify {challengeId, coords, track, dwell} -> captcha_response.
  4. Return the 32-hex response; domain/solution.py nests it as
     solution.data.captcha_response and attaches the UA header the page must
     submit it with.

The response is not a portable token: it is bound to the challenge and the
session that requested it, which is why a missing response is reported as an
expired challenge rather than retried with the same id.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import endpoints as ep
from . import wire


@register_solver
class BasiliskSolver(InteractiveBase):
    """Basilisk click-the-sponsor challenge."""

    info = SolverInfo(
        name="basilisk",
        types=(CaptchaType.BASILISK,),
        http=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "sitekey")
        if ctx.http is None:
            raise ConfigError("basilisk solver needs ctx.http", context={"solver": self.info.name})

        payload = task.payload
        sitekey = str(payload["sitekey"])
        origin = str(_first(payload, "origin", "pageurl", "websiteURL") or task.site_url or "")
        start = time.monotonic()
        headers = {"accept": "application/json", "content-type": "application/json"}
        ua = _first(payload, "user_agent", "userAgent") or task.user_agent
        if ua:
            headers["user-agent"] = str(ua)
        if origin:
            headers["referer"] = origin

        resp = await ctx.http.post(ep.CHALLENGE, json={"siteKey": sitekey, "origin": origin}, headers=headers)
        root = wire.json_object(resp, "basilisk challenge")
        wire.raise_for_failure(root, "basilisk challenge")
        challenge = wire.parse_challenge(root)

        image = await _image(ctx, challenge["image"], headers)
        cv = await self._solve_cv(
            CaptchaType.CLICK,
            task,
            ctx,
            {"image": wire.b64(image), "target": challenge["target"], "seed": payload.get("seed")},
        )
        body = wire.answer_body(challenge, cv, payload.get("seed"))
        body["siteKey"] = sitekey

        vr = await ctx.http.post(ep.VERIFY, json=body, headers=headers)
        vroot = wire.json_object(vr, "basilisk verify")
        wire.raise_for_failure(vroot, "basilisk verify")
        response = wire.find_response(vroot)
        if not response:
            raise ChallengeExpiredError(
                "basilisk verify returned no captcha_response (challenge consumed or rejected)",
                context={"keys": sorted(vroot), "challengeId": challenge["challenge_id"]},
            )

        extra: dict[str, Any] = {}
        if ua:
            extra["headers"] = {"User-Agent": str(ua)}
        return SolverResult(
            token=response,
            extra=extra,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )


async def _image(ctx: SolverContext, url: str, headers: dict[str, str]) -> bytes:
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1])
        except Exception as exc:
            raise UnsolvableError("basilisk image is a broken data URI", context={"head": url[:48]}) from exc
    resp = await ctx.http.get(url, headers=headers)
    wire.raise_for_status(resp, "basilisk image")
    if not resp.body:
        raise UnsolvableError("basilisk image body is empty", context={"url": url})
    return resp.body


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
