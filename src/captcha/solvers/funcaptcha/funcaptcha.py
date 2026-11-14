"""Arkose FunCaptcha dispatcher.

Arkose ships many game variants; from a solver's seat they fall into three
buckets:

  rotate     -> delegated to the ROTATE solver via the registry
  match / 3d -> per-variant CV solvers (not yet implemented)
  tile/misc  -> per-variant solvers (not yet implemented)

This file owns the HTTP transport and the dispatch. The BDA payload is
produced by `bda.py` (or injected via `task.payload['bda']` for tests /
pre-captured sessions).
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import structlog
from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import (
    CaptchaError,
    ChallengeExpiredError,
    SensorError,
    UnsolvableError,
)
from captcha.solvers import Solver, SolverContext, SolverInfo, register_solver

from captcha.solvers.funcaptcha.bda import build_bda_payload

_log = structlog.get_logger(__name__)

ARKOSE_HOST = "https://client-api.arkoselabs.com"
ARKOSE_ENT_HOST_FMT = "https://{subdomain}-api.arkoselabs.com"

API_JS_URL_FMT = "/v2/{pk}/api.js"
PUBLIC_KEY_URL_FMT = "/fc/gt2/public_key/{pk}"
CHALLENGE_URL = "/fc/gc/"
ANSWER_URL = "/fc/ca/"


class _FunCaptchaBase(Solver):
    """Shared dispatch — Enterprise overrides `_host_for`."""

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise CaptchaError("funcaptcha requires ctx.http", context={"task": str(task.id)})
        pk = task.sitekey or (task.payload or {}).get("public_key")
        if not pk:
            raise CaptchaError(
                "funcaptcha missing public_key / sitekey",
                context={"task": str(task.id)},
            )
        if not task.site_url:
            raise CaptchaError("funcaptcha missing site_url", context={"task": str(task.id)})

        host = self._host_for(task)
        payload = task.payload or {}

        # BDA — payload-injected (e.g. captured) wins over live build.
        bda = payload.get("bda")
        if not bda:
            bda = await build_bda_payload(task=task, ctx=ctx, host=host, pk=pk)

        # Open session with Arkose
        session = await self._open_session(ctx=ctx, host=host, pk=pk, bda=bda, task=task)
        token = session.get("token")
        if not token:
            raise ChallengeExpiredError(
                "funcaptcha session open returned no token",
                context={"host": host, "pk": pk, "response_keys": sorted(session.keys())},
            )

        # The token itself may already be the final answer for "challenge
        # suppressed" sessions (score path). If Arkose included `tokenURL`
        # the caller is expected to display a game — dispatch.
        if session.get("tokenURL") is None and session.get("challenge_url") is None:
            return SolverResult(
                token=token,
                solve_mode=SolveMode.HTTP,
                extra={"path": "suppressed", "session": _redact_session(session)},
            )

        game_type = session.get("game_type") or session.get("gameType") or payload.get("game_type")
        return await self._dispatch_game(
            task=task,
            ctx=ctx,
            host=host,
            session=session,
            game_type=str(game_type or "unknown"),
        )

    def _host_for(self, task: Task) -> str:
        return ARKOSE_HOST

    async def _open_session(
        self,
        *,
        ctx: SolverContext,
        host: str,
        pk: str,
        bda: str,
        task: Task,
    ) -> dict[str, Any]:
        """POST /fc/gt2/public_key/<pk> with the BDA + metadata."""
        data = {
            "public_key": pk,
            "site": task.site_url,
            "userbrowser": task.user_agent or "",
            "capi_version": "2.11.0",
            "capi_mode": "inline",
            "style_theme": "default",
            "rnd": _rnd(),
            "bda": bda,
        }
        extra_blob = (task.payload or {}).get("blob")
        if extra_blob:
            data["data[blob]"] = extra_blob

        resp = await ctx.http.post(
            host + PUBLIC_KEY_URL_FMT.format(pk=pk),
            data=data,
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "referer": task.site_url,
                "accept": "application/json",
            },
        )
        if resp.status != 200:
            raise ChallengeExpiredError(
                "funcaptcha session open non-200",
                context={"status": resp.status, "host": host, "pk": pk},
            )
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise SensorError(
                "funcaptcha session body not JSON — Arkose shape drifted",
                context={"host": host, "body_head": resp.text[:120]},
            ) from exc

    async def _dispatch_game(
        self,
        *,
        task: Task,
        ctx: SolverContext,
        host: str,
        session: dict[str, Any],
        game_type: str,
    ) -> SolverResult:
        normalized = game_type.lower().strip()
        if normalized in ("rotate", "3d_rollball_animals", "3d_rollball_objects", "rollball"):
            return await self._solve_rotate(task=task, ctx=ctx, host=host, session=session)

        # FILL: per-variant CV solvers —
        #   - match (pick the odd image) -> CaptchaType.CLASSIFICATION_GRID
        #   - 3d_dice / 3d_cube          -> new dedicated solver, not yet registered
        #   - cardistry / handnumber     -> sibling classification
        # Each dispatches through the registry the same way rotate does; add
        # a branch here once the sibling solver exists and route by
        # `session.game_type`.
        raise CaptchaError(
            "funcaptcha game variant not yet implemented — see runbooks/funcaptcha.md",
            context={"game_type": game_type, "session_token": session.get("token", "")[:40]},
        )

    async def _solve_rotate(
        self,
        *,
        task: Task,
        ctx: SolverContext,
        host: str,
        session: dict[str, Any],
    ) -> SolverResult:
        """Delegate the rotate game to the ROTATE solver via the registry."""
        registry = ctx.extra.get("solver_registry") if isinstance(ctx.extra, dict) else None
        if registry is None:
            raise CaptchaError(
                "funcaptcha rotate needs ctx.extra['solver_registry']",
                context={"task": str(task.id)},
            )
        rotate_cls = registry.pick(CaptchaType.ROTATE)
        if rotate_cls is None:
            raise CaptchaError(
                "no solver registered for CaptchaType.ROTATE — install captcha-solver-rotate",
                context={"type": CaptchaType.ROTATE.value},
            )

        # The rotate solver takes an image + optional target; Arkose returns
        # game assets through `challenge_url` + a signed `session_token`.
        # We pass the whole session through so the rotate solver can do its
        # own asset fetch; it needs the URL list only.
        # FILL: when the rotate solver's payload contract firms up, trim this
        # down to just {"image": <bytes>, "target_angle": <float|None>}.
        rotate_task = Task(
            id=task.id,
            type=CaptchaType.ROTATE,
            payload={
                "arkose_session": session,
                "host": host,
                "challenge_url": session.get("challenge_url") or session.get("tokenURL"),
                "session_token": session.get("session_token") or session.get("token"),
            },
            priority=task.priority,
            preferred_mode=SolveMode.HTTP,
            sitekey=task.sitekey,
            site_url=task.site_url,
            proxy=task.proxy,
            user_agent=task.user_agent,
            language=task.language,
            created_at=task.created_at,
            deadline_ts=task.deadline_ts,
            client_id=task.client_id,
        )

        solver = rotate_cls()
        try:
            await solver.prepare(ctx)
            rotate_result = await solver.solve_http(rotate_task, ctx)
        finally:
            try:
                await solver.shutdown()
            except Exception as exc:  # noqa: BLE001
                _log.warning("rotate_solver.shutdown_failed", error=str(exc))

        angle = rotate_result.angle
        if angle is None:
            raise UnsolvableError(
                "rotate solver returned no angle",
                context={"host": host, "session": session.get("token", "")[:40]},
            )

        # Submit the answer to Arkose and collect the final token.
        answer_payload = {
            "session_token": session.get("session_token") or session.get("token"),
            "game_token": session.get("token"),
            "guess": json.dumps([{"index": int(round(angle)) % 360}]),
        }
        answer_resp = await ctx.http.post(
            host + ANSWER_URL,
            data=answer_payload,
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "referer": task.site_url,
                "accept": "application/json",
            },
        )
        if answer_resp.status != 200:
            raise ChallengeExpiredError(
                "funcaptcha answer non-200",
                context={"status": answer_resp.status, "host": host},
            )
        try:
            answer = answer_resp.json()
        except Exception as exc:  # noqa: BLE001
            raise SensorError(
                "funcaptcha answer body not JSON",
                context={"body_head": answer_resp.text[:120]},
            ) from exc

        solved = answer.get("solved")
        if not solved:
            raise UnsolvableError(
                "funcaptcha rotate rejected guess",
                context={"answer": _redact_session(answer)},
            )

        final_token = answer.get("token") or session.get("token")
        return SolverResult(
            token=final_token,
            angle=angle,
            solve_mode=SolveMode.HTTP,
            extra={"path": "rotate", "answer": _redact_session(answer)},
        )


@register_solver
class FunCaptchaSolver(_FunCaptchaBase):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="funcaptcha",
        types=(CaptchaType.FUNCAPTCHA,),
        http=True,
        browser=False,
        needs_js_runtime=True,
        experimental=False,
        version="0.1.0",
    )


@register_solver
class FunCaptchaEnterpriseSolver(_FunCaptchaBase):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="funcaptcha_enterprise",
        types=(CaptchaType.FUNCAPTCHA_ENTERPRISE,),
        http=True,
        browser=False,
        needs_js_runtime=True,
        experimental=False,
        version="0.1.0",
    )

    def _host_for(self, task: Task) -> str:
        payload = task.payload or {}
        subdomain = payload.get("subdomain") or payload.get("surl")
        if subdomain:
            return ARKOSE_ENT_HOST_FMT.format(subdomain=str(subdomain).strip("."))
        # FILL: enterprise sitekeys are tied to a customer subdomain; pin the
        # mapping in a config file (customer_pk -> subdomain) or make the
        # gateway pass it through under payload['subdomain'].
        return ARKOSE_HOST


# ---- helpers --------------------------------------------------------------


def _rnd() -> str:
    """Arkose's `rnd` is a 17-digit float emitted by Math.random() — we
    emit the same shape without actually seeding V8."""
    import random

    return f"{random.random():.17f}"


def _redact_session(session: dict[str, Any]) -> dict[str, Any]:
    """Trim long opaque fields before logging."""
    out: dict[str, Any] = {}
    for k, v in session.items():
        if isinstance(v, str) and len(v) > 80:
            out[k] = v[:40] + "…"
        else:
            out[k] = v
    return out
