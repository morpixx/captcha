"""Twitch Integrity solver.

Twitch gates GQL behind a short-lived Client-Integrity JWT minted by
POST https://gql.twitch.tv/integrity. The JWT is produced by the Kasada
KPSDK bootstrap the player loads, so there are two honest paths:

1. **js-runtime** -- run the captured (or fetched) p.js in ctx.js_runtime,
   let it patch fetch, harvest the x-kpsdk-* headers, then POST.
2. **browser lane** -- load the player page and let its own fetch call mint
   the token; used when the HTTP path cannot run the bootstrap.

Answers are a single token plus the server's expiration, which is what the
2captcha token/userAgent shape expects (docs/protocol.md).
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import (
    ChallengeExpiredError,
    ConfigError,
    NeedsBrowserError,
    SensorError,
)
from captcha.domain.ports import BrowserSpec
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase

from . import browser as page
from . import payload as fields
from . import wire


class TwitchIntegritySolver(AntibotBase):
    """Twitch Client-Integrity token."""

    engine = AntibotEngine.TWITCH

    info: ClassVar[SolverInfo] = SolverInfo(
        name="twitch_integrity",
        types=(CaptchaType.TWITCH_INTEGRITY,),
        http=True,
        browser=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        payload = self.payload_or_fail(task)
        precomputed = fields.client_integrity(payload)
        if precomputed:
            return self._answer(precomputed, task, payload, started)

        self.require_ctx(ctx, "http", "sensors", "js_runtime")
        bootstrap = fields.challenge_js(payload)
        if not bootstrap:
            bootstrap = await self._fetch_bootstrap(ctx, fields.script_url(payload))
        if not bootstrap:
            raise NeedsBrowserError(
                "twitch integrity needs the KPSDK bootstrap; escalate to the browser lane",
                context={"type": task.type.value},
            )

        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            timestamp_ms=fields.timestamp_ms(payload),
            headers=fields.request_headers(task, payload),
            extra={
                "p_script": bootstrap,
                "script_url": fields.script_url(payload),
                "client_id": fields.client_id(payload),
                "ua": fields.user_agent(task, payload),
            },
        )
        plan = await builder.build(build_ctx)
        raw = await ctx.js_runtime.exec_script(
            plan["script"],
            plan.get("globals") or {},
            timeout_ms=fields.js_timeout_ms(task, payload),
        )
        headers = wire.merge_headers(plan["headers"], wire.capture_headers(raw))
        response = await self.post_sensor(ctx, plan["url"], b"", headers=headers)
        status = int(getattr(response, "status", 0) or 0)
        if status in (401, 403):
            raise SensorError(
                "twitch /integrity rejected the device or headers",
                context={"status": status, "headers": sorted(headers)},
            )
        if status == 0 or status >= 500:
            raise SensorError("twitch /integrity is unavailable", context={"status": status})
        if status >= 400:
            raise ChallengeExpiredError(
                "twitch /integrity refused the challenge",
                context={"status": status},
            )
        token, expiration = wire.parse_response(response)
        return self._answer(token, task, payload, started, expiration=expiration)

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        payload = self.payload_or_fail(task)
        precomputed = fields.client_integrity(payload)
        if precomputed:
            return self._answer(precomputed, task, payload, started, mode=SolveMode.BROWSER)

        provider = getattr(ctx, "browser", None)
        if provider is None:
            raise ConfigError(
                "twitch integrity browser lane needs ctx.browser",
                context={"type": task.type.value},
            )
        page_url = fields.page_url(task, payload)
        headers = fields.request_headers(task, payload)
        spec = BrowserSpec(proxy=task.proxy, label=f"twitch_integrity:{task.id}")
        async with page.open_session(provider, spec) as session:
            await page.navigate(session, page_url)
            raw = await page.evaluate(session, page.integrity_js(headers))
        token, expiration = wire.parse_response(raw)
        return self._answer(token, task, payload, started, expiration=expiration, mode=SolveMode.BROWSER)

    # -- helpers ----------------------------------------------------------

    async def _fetch_bootstrap(self, ctx: SolverContext, url: str) -> str:
        """GET p.js when the client did not inline it."""
        if not url:
            return ""
        response = await ctx.http.get(url)
        status = int(getattr(response, "status", 0) or 0)
        if status and not 200 <= status < 300:
            raise SensorError(
                "twitch KPSDK bootstrap fetch failed",
                context={"status": status, "url": url},
            )
        body = getattr(response, "body", b"")
        if isinstance(body, (bytes, bytearray)):
            return bytes(body).decode("utf-8", "replace")
        text = getattr(response, "text", "")
        return text if isinstance(text, str) else str(body or "")

    def _answer(
        self,
        token: str,
        task: Task,
        payload: dict[str, Any],
        started: float,
        *,
        expiration: int | None = None,
        mode: SolveMode = SolveMode.HTTP,
    ) -> SolverResult:
        claims = wire.decode_claims(token)
        if wire.flagged(claims):
            raise SensorError(
                "twitch flagged the integrity token as bot traffic",
                context={"claims": sorted(claims)},
            )
        extra: dict[str, Any] = {"userAgent": fields.user_agent(task, payload)}
        device = fields.device_id(payload)
        if device:
            extra["deviceId"] = device
        if expiration:
            extra["expiration"] = expiration
        return SolverResult(
            token=token,
            extra={key: value for key, value in extra.items() if value},
            solve_mode=mode,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
