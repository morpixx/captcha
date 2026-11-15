"""Arkose FunCaptcha: one HTTP session, the challenge it hands back, the token.

Flow (public RE, 2026-07):

    1. GET  {surl}/v2/{pk}/api.js      capi version, build id, RSA key
    2. BDA   sensor / recorded / JS hook, sealed by RSA-OAEP + AES-GCM
    3. POST /fc/gt2/public_key/{pk}    session token (sup=1 means "no game")
    4. GET  /fc/gfct/                  challengeID + images + instruction
    5. POST /fc/ca/                    answer; repeat on a re-challenge

Variants are delegated, not re-implemented: rotate to the geometry solver, grid
and image matching to the classification head (variants.py), rounds to session.py.
A live game we cannot model leaves as NeedsBrowserError so the worker moves the
envelope to the browser lane once, where the real widget answers its own
challenge.

Enterprise subclasses only differ in which host serves api.js: a customer
subdomain (surl) rather than the shared client-api one.
"""

from __future__ import annotations

from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError
from captcha.solvers import Solver, SolverContext, SolverInfo, register_solver

from . import bda as bda_mod
from . import browser, session, settings, variants, wire

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)


class _FunCaptchaBase(Solver):
    """Shared flow; subclasses only choose the verify host."""

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise ConfigError("funcaptcha needs ctx.http", context={"task": str(task.id)})
        payload = task.payload or {}
        public_key = settings.public_key_for(task)
        surl = self._surl(task)
        user_agent = task.user_agent or str(payload.get("userAgent") or DEFAULT_UA)

        cfg = await settings.fetch_settings(
            ctx,
            surl=surl,
            public_key=public_key,
            user_agent=user_agent,
            pinned_rsa=_pinned_rsa(payload, ctx),
        )
        fingerprint = ctx.fingerprint_store.pick() if ctx.fingerprint_store is not None else None
        sealed = await bda_mod.build_bda(task, ctx, cfg=cfg, fingerprint=fingerprint)

        opened = await wire.open_session(
            ctx,
            cfg,
            task,
            bda_value=sealed.value,
            bda_field=sealed.field,
            user_agent=user_agent,
            blob=wire.blob_from(payload),
        )
        token = opened.get("token")
        if not isinstance(token, str) or not token:
            raise ChallengeExpiredError(
                "arkose session opened without a token",
                context={"keys": sorted(opened), "surl": surl, "public_key": public_key},
            )

        if wire.split_token(token).suppressed:
            return session.result(token, user_agent, path="suppressed", extra={"bda_source": sealed.source})

        challenge = await wire.fetch_challenge(ctx, cfg, task, token, user_agent=user_agent)
        if not variants.challenge_images(challenge):
            # The endpoint already answered this session; a game round has images.
            return session.result(token, user_agent, path="token-only", extra={"bda_source": sealed.source})

        return await session.play(
            task=task,
            ctx=ctx,
            cfg=cfg,
            payload=payload,
            token=token,
            user_agent=user_agent,
            challenge=challenge,
            bda_source=sealed.source,
        )

    async def solve_browser(self, task: Task, ctx: SolverContext) -> SolverResult:
        return await browser.solve(task, ctx, solver=self)

    def _surl(self, task: Task) -> str:
        return settings.surl_for(task)


@register_solver
class FunCaptchaSolver(_FunCaptchaBase):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="funcaptcha",
        types=(CaptchaType.FUNCAPTCHA,),
        http=True,
        browser=True,
        needs_js_runtime=False,
        experimental=False,
        version="0.2.0",
    )


@register_solver
class FunCaptchaEnterpriseSolver(_FunCaptchaBase):
    """Enterprise: api.js and the token endpoint live on the customer subdomain."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="funcaptcha_enterprise",
        types=(CaptchaType.FUNCAPTCHA_ENTERPRISE,),
        http=True,
        browser=True,
        needs_js_runtime=False,
        experimental=False,
        version="0.2.0",
    )


def _pinned_rsa(payload: dict[str, Any], ctx: SolverContext) -> str:
    pinned = payload.get("rsa_public_key")
    if isinstance(pinned, str) and pinned.strip():
        return pinned.strip()
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    value = extra.get("arkose_rsa_key")
    return value.strip() if isinstance(value, str) else ""
