"""Hunt interactive captcha: server API over HTTP, widget SDK in the browser.

Two documented ways to get a Hunt token:

* server API - the page hands the client a per-session JWT and a host; init
  returns an image plus a prompt, verify takes the click (or slide) and returns
  the token. That is solve_http below.
* widget - the page exposes widgetUrl and the in-page SDK signs its own
  requests. The HTTP path cannot fabricate the X-HD fingerprint, so it raises
  NeedsBrowserError and browser.py runs the widget in the browser lane.

The result shape is nested by contract: solution.data.token. Returning a flat
token would look like a success and be unreadable by a 2captcha client.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import (
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    UnsolvableError,
)
from captcha.solvers.base import SolverContext, SolverInfo
from captcha.solvers.registry import register_solver

from .._base import InteractiveBase
from . import browser as browser_lane
from . import endpoints as ep
from . import wire


@register_solver
class HuntSolver(InteractiveBase):
    """Hunt captcha: server API when a JWT is supplied, widget otherwise."""

    info = SolverInfo(
        name="hunt",
        types=(CaptchaType.HUNT,),
        http=True,
        browser=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = task.payload
        jwt = _first(payload, "session_jwt", "sessionJwt", "jwt")
        host = _first(payload, "host", "api_host", "apiHost")
        if not jwt:
            if _widget_available(task):
                # Contract signal: the consumer reroutes the envelope. worker_cv
                # does not implement that hop yet (worker_http does); the
                # browser lane is still reachable via preferred_mode=BROWSER.
                raise NeedsBrowserError(
                    "hunt widget mode needs the browser lane: the SDK signs its own requests",
                    context={"need": "session_jwt", "widget": True},
                )
            raise UnsolvableError(
                "hunt needs host + session_jwt, or widget_url for the browser lane",
                context={"keys": sorted(payload)},
            )
        if not host:
            raise UnsolvableError("missing payload fields: ['host']", context={"have": sorted(payload)})
        if ctx.http is None:
            raise ConfigError("hunt solver needs ctx.http", context={"solver": self.info.name})

        start = time.monotonic()
        base = str(host).rstrip("/")
        if "://" not in base:
            base = "https://" + base
        headers = {"accept": "application/json", "content-type": "application/json"}
        headers["authorization"] = f"Bearer {jwt}"
        ua = _first(payload, "user_agent", "userAgent") or task.user_agent
        if ua:
            headers["user-agent"] = str(ua)

        resp = await ctx.http.post(base + ep.PATH_INIT, json={}, headers=headers)
        root = wire.json_object(resp, "hunt init")
        wire.raise_for_failure(root, "hunt init")
        challenge = wire.parse_challenge(root)

        kind = wire.variant_kind(challenge["variant"], bool(challenge["piece"]), challenge["prompt"])
        image = await _image(ctx, challenge["image"], headers)
        if kind == "slider":
            if not challenge["piece"]:
                raise UnsolvableError(
                    "hunt slider challenge has no piece image",
                    context={"variant": challenge["variant"], "keys": sorted(root)},
                )
            cv_payload: dict[str, Any] = {
                "bg": wire.b64(image),
                "piece": wire.b64(await _image(ctx, challenge["piece"], headers)),
                "seed": payload.get("seed"),
            }
            cv_kind = CaptchaType.SLIDER
        else:
            if not challenge["prompt"]:
                raise UnsolvableError(
                    "hunt click challenge has no target prompt",
                    context={"variant": challenge["variant"], "keys": sorted(root)},
                )
            cv_payload = {"image": wire.b64(image), "target": challenge["prompt"], "seed": payload.get("seed")}
            cv_kind = CaptchaType.CLICK

        cv = await self._solve_cv(cv_kind, task, ctx, cv_payload)
        body = wire.answer_body(challenge, cv, kind, payload.get("seed"))
        vr = await ctx.http.post(base + ep.PATH_VERIFY, json=body, headers=headers)
        vroot = wire.json_object(vr, "hunt verify")
        wire.raise_for_failure(vroot, "hunt verify")
        token, block = wire.find_token(vroot)
        if not token:
            raise ChallengeExpiredError(
                "hunt verify returned no token (challenge consumed or rejected)",
                context={"keys": sorted(vroot), "huntId": challenge["challenge_id"]},
            )

        data = block if block.get("token") else {"token": token}
        return SolverResult(
            token=token,
            extra={"data": data},
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.monotonic() - start) * 1000.0,
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        return await browser_lane.solve(task, ctx, solver=self)


def _widget_available(task: Task) -> bool:
    payload = task.payload
    return any(payload.get(key) for key in ("widget_url", "widgetUrl", "pageurl", "websiteURL")) or bool(
        task.site_url
    )


async def _image(ctx: SolverContext, url: str, headers: dict[str, str]) -> bytes:
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1])
        except Exception as exc:
            raise UnsolvableError("hunt image is a broken data URI", context={"head": url[:48]}) from exc
    resp = await ctx.http.get(url, headers=headers)
    wire.raise_for_status(resp, "hunt image")
    if not resp.body:
        raise UnsolvableError("hunt image body is empty", context={"url": url})
    return resp.body


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
