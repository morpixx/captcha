"""Twitch Integrity solver.

Twitch runs a KPSDK-adjacent integrity check in-browser: the GQL client
posts an empty body to `/integrity` with a `Client-Integrity` JWT
derived from browser probes, and receives a longer-lived integrity token
plus an expiry. We port that derivation into the sensor builder.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import SolverResult, Task
from captcha.domain.enums import AntibotEngine, CaptchaType
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase


class TwitchIntegritySolver(AntibotBase):
    """Twitch Client-Integrity token."""

    engine = AntibotEngine.TWITCH

    info: ClassVar[SolverInfo] = SolverInfo(
        name="twitch_integrity",
        types=(CaptchaType.TWITCH_INTEGRITY,),
        http=True,

        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self.require_ctx(ctx, "http", "sensors", "js_runtime", "fingerprint_store")
        payload = self.payload_or_fail(task)
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            headers=payload.get("headers"),
            extra={
                "device_id": payload.get("device_id"),
                "client_session_id": payload.get("client_session_id"),
                "client_version": payload.get("client_version"),
                "ua": task.user_agent,
            },
        )
        _ = builder, build_ctx
        # FILL: client_integrity = await builder.build(build_ctx)  # JWT str
        # FILL: resp = await ctx.http.post(INTEGRITY_URL, headers={
        #     "client-id": WEB_CLIENT_ID,
        #     "client-integrity": client_integrity,
        #     "x-device-id": payload["device_id"],
        #     ...})
        # FILL: body = resp.json(); return SolverResult(token=body["token"],
        #     extra={"expiration": body["expiration"]})
        raise self.not_implemented(
            "twitch_integrity",
            next_step="port Twitch's client-integrity JWT signer; see runbooks/twitch_integrity.md",
        )
