"""reCAPTCHA v3 / v3 Enterprise: HTTP anchor -> protobuf reload -> rresp.

No browser, no Node. The reload body uses fields from the public grecaptcha
bundle (v=1, c=2, reason=6, action=8, k=14); Google answers with an
XSSI-guarded `["rresp","<token>",...]` array.

`bg` / fingerprint telemetry is optional. `min_score` is validated and echoed,
never invented: the token's score is only visible server-side at /siteverify.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError, UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo, register_solver
from captcha.solvers.recaptcha._common import (
    GOOGLE_HOST,
    Endpoints,
    encode_origin,
    endpoints_for,
    extract_anchor_token,
    extract_rresp,
    initial_version,
    random_cb,
    resolve_version,
)
from captcha.solvers.recaptcha._proto import encode_reload_body

_DEFAULT_ACTION = "verify"
_DEFAULT_MIN_SCORE = 0.3


class _RecaptchaV3Base(Solver):
    _ENTERPRISE: ClassVar[bool] = False

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise ConfigError("reCAPTCHA v3 requires ctx.http", context={"task": str(task.id)})
        sitekey = task.sitekey
        if not sitekey:
            raise UnsolvableError(
                "reCAPTCHA v3 payload missing sitekey",
                context={"task": str(task.id)},
            )
        if not task.site_url:
            raise UnsolvableError(
                "reCAPTCHA v3 payload missing site_url",
                context={"task": str(task.id)},
            )

        payload = dict(task.payload or {})
        action = _action(payload)
        min_score = _min_score(payload)
        endpoints = endpoints_for(self._ENTERPRISE)
        version = initial_version(task)

        anchor = await _fetch_anchor(task, ctx, endpoints, sitekey=sitekey, version=version)
        anchor_token = extract_anchor_token(anchor.text)
        if not anchor_token:
            refreshed = await resolve_version(task, ctx, endpoints)
            if refreshed != version:
                version = refreshed
                anchor = await _fetch_anchor(task, ctx, endpoints, sitekey=sitekey, version=version)
                anchor_token = extract_anchor_token(anchor.text)
        if not anchor_token:
            raise ChallengeExpiredError(
                "reCAPTCHA v3 anchor returned no token — release slug or origin stale",
                context={"status": anchor.status, "url": anchor.final_url},
            )

        body = encode_reload_body(
            version=version,
            token=anchor_token,
            sitekey=sitekey,
            action=action,
            reason="q",
            bg=_bg(payload, ctx),
            fingerprint=_fingerprint(payload, ctx),
        )
        reload_resp = await ctx.http.post(
            endpoints.reload,
            params={"k": sitekey},
            data=body,
            headers={
                "content-type": "application/x-protobuffer",
                "accept": "*/*",
                "origin": GOOGLE_HOST,
                "referer": anchor.final_url or task.site_url,
            },
        )
        token = extract_rresp(reload_resp.text)
        if not token:
            raise UnsolvableError(
                "reCAPTCHA v3 reload returned no rresp token",
                context={
                    "status": reload_resp.status,
                    "action": action,
                    "body_head": reload_resp.text[:120],
                },
            )

        return SolverResult(
            token=token,
            extra={"action": action, "min_score": min_score},
        )


@register_solver
class RecaptchaV3Solver(_RecaptchaV3Base):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="recaptcha_v3",
        types=(CaptchaType.RECAPTCHA_V3,),
        http=True,
        needs_js_runtime=False,
        experimental=True,
        version="0.2.0",
    )


@register_solver
class RecaptchaV3EnterpriseSolver(_RecaptchaV3Base):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="recaptcha_v3_enterprise",
        types=(CaptchaType.RECAPTCHA_V3_ENTERPRISE,),
        http=True,
        needs_js_runtime=False,
        experimental=True,
        version="0.2.0",
    )

    _ENTERPRISE = True


async def _fetch_anchor(
    task: Task,
    ctx: SolverContext,
    endpoints: Endpoints,
    *,
    sitekey: str,
    version: str,
):
    site_url = task.site_url or ""
    return await ctx.http.get(
        endpoints.anchor,
        params={
            "ar": "1",
            "k": sitekey,
            "co": encode_origin(site_url),
            "hl": task.language or "en",
            "v": version,
            "size": "invisible",
            "cb": random_cb(),
        },
        headers={"referer": site_url, "accept": "text/html,application/xhtml+xml"},
    )


def _action(payload: Mapping[str, Any]) -> str:
    raw = payload.get("action", _DEFAULT_ACTION)
    if not isinstance(raw, str) or not raw.strip():
        raise UnsolvableError(
            "reCAPTCHA v3 action must be a non-empty string",
            context={"action": raw},
        )
    return raw.strip()


def _min_score(payload: Mapping[str, Any]) -> float:
    raw = payload.get("min_score", _DEFAULT_MIN_SCORE)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise UnsolvableError(
            "reCAPTCHA v3 min_score must be a number in [0, 1]",
            context={"min_score": raw},
        ) from exc
    if not 0.0 <= value <= 1.0:
        raise UnsolvableError(
            "reCAPTCHA v3 min_score must be a number in [0, 1]",
            context={"min_score": raw},
        )
    return value


def _bg(payload: Mapping[str, Any], ctx: SolverContext) -> str | None:
    value = payload.get("bg")
    if value is None and isinstance(ctx.extra, dict):
        value = ctx.extra.get("recaptcha_bg")
    return value if isinstance(value, str) and value else None


def _fingerprint(payload: Mapping[str, Any], ctx: SolverContext) -> Mapping[str | int, object] | None:
    value = payload.get("fingerprint")
    if value is None and isinstance(ctx.extra, dict):
        value = ctx.extra.get("recaptcha_fingerprint")
    return value if isinstance(value, dict) and value else None
