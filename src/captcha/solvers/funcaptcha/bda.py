"""Arkose BDA payload builder: sensor snapshot -> envelope -> one form field.

The token endpoint refuses a session without a browser-data blob. Three honest
ways to get one, in order of preference:

  1. payload['bda_plaintext'] — a recorded BDA, used verbatim. Tests, replays
     and operators who captured a clean session themselves.
  2. ctx.extra['funcaptcha_bda_builder'] — an async hook that returns the BDA
     plaintext. This is where a jssandbox run of api.js belongs: the worker owns
     the runtime, the solver never spawns one, and a failure is a JSRuntimeError
     from the hook, not a silently faked blob.
  3. the synthesised sensor (sensor.py) — derived from a sampled device plus the
     settings we scraped from api.js.

Whatever path produced it, SolverResult.extra['bda_source'] says which one, so
a run is never mistaken for a browser mint.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.errors import ConfigError, SensorError
from captcha.solvers import Solver, SolverContext, SolverInfo, register_solver

from . import envelope, sensor, settings
from .settings import ArkoseSettings


@dataclass(frozen=True, slots=True)
class BDA:
    """One encrypted BDA plus where the plaintext came from."""

    value: str
    field: str
    plaintext: str
    source: str


async def build_bda(
    task: Task,
    ctx: SolverContext,
    *,
    cfg: ArkoseSettings,
    fingerprint: Any = None,
    rng: random.Random | None = None,
) -> BDA:
    """Produce the encrypted BDA field for this task."""
    payload = task.payload or {}
    injected = payload.get("bda_plaintext")
    if isinstance(injected, str) and injected:
        return _seal(injected, cfg, source="recorded")

    builder = _builder_hook(ctx)
    if builder is not None:
        produced = await builder(task=task, settings=cfg, ctx=ctx)
        if not isinstance(produced, str) or not produced:
            raise SensorError(
                "funcaptcha BDA hook returned no plaintext",
                context={"got": type(produced).__name__},
            )
        return _seal(produced, cfg, source="js")

    snapshot = sensor.build_snapshot(
        sensor.SensorConfig(
            surl=cfg.surl,
            language=task.language or "en-US",
            title=str(payload.get("title") or ""),
            location_href=task.site_url or "",
            document_referrer=payload.get("document_referrer"),
            enforcement_hash=cfg.enforcement_hash,
        ),
        build_id=cfg.build_id,
        rng=rng,
        fingerprint=fingerprint,
    )
    return _seal(sensor.marshal_bda(snapshot), cfg, source="sensor")


def _seal(plaintext: str, cfg: ArkoseSettings, *, source: str) -> BDA:
    value = envelope.encrypt_bda(plaintext, settings.require_rsa(cfg))
    return BDA(value=value, field=cfg.bda_field, plaintext=plaintext, source=source)


def _builder_hook(ctx: SolverContext) -> Any:
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    return extra.get("funcaptcha_bda_builder")


@register_solver
class FunCaptchaBDASolver(Solver):
    """BDA-only solver — one encrypted browser-data blob, no Arkose session.

    The gateway drives the transport itself and only needs the blob the loader
    would have produced. The token stays empty on purpose.
    """

    info: ClassVar[SolverInfo] = SolverInfo(
        name="funcaptcha_bda",
        types=(CaptchaType.FUNCAPTCHA_BDA,),
        http=True,
        needs_js_runtime=False,
        experimental=True,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise ConfigError("funcaptcha_bda needs ctx.http", context={"task": str(task.id)})
        payload = task.payload or {}
        public_key = settings.public_key_for(task)
        surl = settings.surl_for(task)
        ua = task.user_agent or str(payload.get("userAgent") or "")
        cfg = await settings.fetch_settings(
            ctx,
            surl=surl,
            public_key=public_key,
            user_agent=ua,
            pinned_rsa=_pinned_rsa(payload, ctx),
        )
        fingerprint = ctx.fingerprint_store.pick() if ctx.fingerprint_store is not None else None
        bda = await build_bda(task, ctx, cfg=cfg, fingerprint=fingerprint)
        return SolverResult(
            token="",
            solve_mode=SolveMode.HTTP,
            extra={
                "bda": bda.value,
                "bda_field": bda.field,
                "bda_source": bda.source,
                "public_key": public_key,
                "surl": surl,
                "capi_version": cfg.capi_version,
                "build_id": cfg.build_id,
                "userAgent": ua,
            },
        )


def _pinned_rsa(payload: dict[str, Any], ctx: SolverContext) -> str:
    pinned = payload.get("rsa_public_key")
    if isinstance(pinned, str) and pinned.strip():
        return pinned.strip()
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    from_extra = extra.get("arkose_rsa_key")
    return from_extra.strip() if isinstance(from_extra, str) else ""
