"""reCAPTCHA v2 audio-bypass solver (shared across v2, v2-invisible, v2-callback).

The audio-bypass path grades the submitted transcript, not behavior, so no
`bg` sensor is needed. The workflow:

    anchor  -> hidden `recaptcha-token`
    reload  -> swap grid challenge for audio (`reason=q`)
    reload  -> confirm audio choice (`reason=a`)
    payload -> MP3 download
    audio   -> transcript (delegated to the `AUDIO` solver)
    userverify -> final `g-recaptcha-response`

Audio transcription is delegated: the worker injects a `SolverRegistry`
under `ctx.extra['solver_registry']`. We pick the solver registered for
`CaptchaType.AUDIO` and feed it the raw MP3 bytes. Enterprise reuses the
exact same pipeline — only the base path flips.
"""

from __future__ import annotations

import base64
import secrets
from typing import Any, ClassVar
from urllib.parse import urlparse

import structlog
from captcha_common import CaptchaType, SolverResult, Task
from captcha_common.enums import SolveMode
from captcha_common.errors import (
    CaptchaError,
    ChallengeExpiredError,
    SensorError,
    UnsolvableError,
)
from captcha_solver_base import Solver, SolverContext, SolverInfo, register_solver

_log = structlog.get_logger(__name__)

# Endpoints. One block, patch day touches here first.
_API_HOST = "https://www.google.com"
ANCHOR_URL = _API_HOST + "/recaptcha/api2/anchor"
RELOAD_URL = _API_HOST + "/recaptcha/api2/reload"
PAYLOAD_URL = _API_HOST + "/recaptcha/api2/payload"
USERVERIFY_URL = _API_HOST + "/recaptcha/api2/userverify"

ANCHOR_URL_ENT = _API_HOST + "/recaptcha/enterprise/anchor"
RELOAD_URL_ENT = _API_HOST + "/recaptcha/enterprise/reload"
PAYLOAD_URL_ENT = _API_HOST + "/recaptcha/enterprise/payload"
USERVERIFY_URL_ENT = _API_HOST + "/recaptcha/enterprise/userverify"

# FILL: pin to the current build hash; dump from a live Google page's
# /recaptcha/api.js?render=... redirect. Stale `v` returns 403.
API_VERSION = "DKw2Q4K7L4-p0e50T4ieYFkC"


def _co(site_url: str) -> str:
    """`co` param: base64url(`host:port`) without padding."""
    parsed = urlparse(site_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return base64.urlsafe_b64encode(f"{host}:{port}".encode()).rstrip(b"=").decode()


def _cb() -> str:
    """`cb` is a 12-char lowercase+digits token the client minted."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def _extract_anchor_token(html: str) -> str | None:
    marker = 'id="recaptcha-token"'
    idx = html.find(marker)
    if idx == -1:
        return None
    value_marker = 'value="'
    v = html.find(value_marker, idx)
    if v == -1:
        return None
    start = v + len(value_marker)
    end = html.find('"', start)
    if end <= start:
        return None
    return html[start:end]


def _extract_reload_field(body: str, index: int = 1) -> str | None:
    """Parse the JS array Google returns from /reload and /userverify.

    Body looks like: `)]}'["<field0>","<field1>",<...>]`
    We trim the anti-XSSI prefix and read the N-th quoted literal (0-based).
    """
    s = body.lstrip()
    if s.startswith(")]}'"):
        s = s[4:]
    s = s.lstrip()
    if not s.startswith("["):
        return None
    # scan quoted strings
    i = 0
    count = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == '"':
            j = i + 1
            buf: list[str] = []
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    buf.append(s[j + 1])
                    j += 2
                    continue
                buf.append(s[j])
                j += 1
            if j >= n:
                return None
            if count == index:
                return "".join(buf)
            count += 1
            i = j + 1
            continue
        i += 1
    return None


def _pick_audio_solver(ctx: SolverContext) -> type[Solver]:
    """Look the audio transcription solver up through the registry handed in
    via `ctx.extra['solver_registry']`.

    Hard-wiring an import would couple this package to captcha-solver-audio;
    going through the registry lets the worker pick which audio backend is
    live.
    """
    registry = ctx.extra.get("solver_registry") if isinstance(ctx.extra, dict) else None
    if registry is None:
        raise CaptchaError(
            "recaptcha v2 solver needs ctx.extra['solver_registry'] to find the audio solver",
            context={"missing": "solver_registry"},
        )
    audio_cls = registry.pick(CaptchaType.AUDIO)
    if audio_cls is None:
        raise CaptchaError(
            "no solver registered for CaptchaType.AUDIO — install captcha-solver-audio",
            context={"type": CaptchaType.AUDIO.value},
        )
    return audio_cls


class _RecaptchaV2Base(Solver):
    _ANCHOR: ClassVar[str] = ANCHOR_URL
    _RELOAD: ClassVar[str] = RELOAD_URL
    _PAYLOAD: ClassVar[str] = PAYLOAD_URL
    _USERVERIFY: ClassVar[str] = USERVERIFY_URL
    _ANCHOR_SIZE: ClassVar[str] = "normal"

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        if ctx.http is None:
            raise CaptchaError("recaptcha v2 requires ctx.http", context={"task": str(task.id)})
        if not task.sitekey:
            raise CaptchaError("recaptcha v2 payload missing sitekey", context={"task": str(task.id)})
        if not task.site_url:
            raise CaptchaError("recaptcha v2 payload missing site_url", context={"task": str(task.id)})

        language = task.language or "en"
        co = _co(task.site_url)
        anchor_size = "invisible" if task.type in (
            CaptchaType.RECAPTCHA_V2_INVISIBLE,
            CaptchaType.RECAPTCHA_V2_CALLBACK,
        ) else self._ANCHOR_SIZE

        anchor_params = {
            "ar": "1",
            "k": task.sitekey,
            "co": co,
            "hl": language,
            "v": API_VERSION,
            "size": anchor_size,
            "cb": _cb(),
        }
        anchor_resp = await ctx.http.get(
            self._ANCHOR,
            params=anchor_params,
            headers={"referer": task.site_url},
        )
        token = _extract_anchor_token(anchor_resp.text)
        if not token:
            raise SensorError(
                "recaptcha v2 anchor returned no token — API_VERSION likely stale",
                context={"status": anchor_resp.status, "url": anchor_resp.final_url},
            )

        # swap to audio challenge
        reload_headers = {
            "content-type": "application/x-www-form-urlencoded",
            "referer": anchor_resp.final_url or task.site_url,
        }
        swap_body = {
            "v": API_VERSION,
            "reason": "q",
            "c": token,
            "sa": "",
            "ds": "[]",
        }
        swap_resp = await ctx.http.post(
            self._RELOAD,
            params={"k": task.sitekey},
            data=swap_body,
            headers=reload_headers,
        )
        swapped_token = _extract_reload_field(swap_resp.text, index=1)
        if not swapped_token:
            raise ChallengeExpiredError(
                "recaptcha v2 refused audio swap — session likely burned",
                context={"status": swap_resp.status, "body_head": swap_resp.text[:120]},
            )

        # confirm audio
        confirm_body = {
            "v": API_VERSION,
            "reason": "a",
            "c": swapped_token,
            "sa": "",
        }
        confirm_resp = await ctx.http.post(
            self._RELOAD,
            params={"k": task.sitekey},
            data=confirm_body,
            headers=reload_headers,
        )
        audio_token = _extract_reload_field(confirm_resp.text, index=1)
        if not audio_token:
            raise ChallengeExpiredError(
                "recaptcha v2 refused audio confirm",
                context={"status": confirm_resp.status, "body_head": confirm_resp.text[:120]},
            )

        # download MP3
        audio_resp = await ctx.http.get(
            self._PAYLOAD,
            params={"c": audio_token, "k": task.sitekey, "id": "2"},
            headers={"referer": task.site_url, "accept": "audio/mpeg,audio/*"},
        )
        if audio_resp.status != 200 or not audio_resp.body:
            raise ChallengeExpiredError(
                "recaptcha v2 audio payload empty",
                context={"status": audio_resp.status, "bytes": len(audio_resp.body)},
            )

        transcript = await self._transcribe(audio_resp.body, task=task, ctx=ctx)
        if not transcript:
            raise UnsolvableError(
                "audio solver returned empty transcript",
                context={"bytes": len(audio_resp.body)},
            )

        verify_body = {
            "v": API_VERSION,
            "c": audio_token,
            "response": transcript,
            "t": "0",
            "ct": "0",
            "bg": "",
        }
        verify_resp = await ctx.http.post(
            self._USERVERIFY,
            params={"k": task.sitekey},
            data=verify_body,
            headers=reload_headers,
        )
        final_token = _extract_reload_field(verify_resp.text, index=1)
        if not final_token:
            raise UnsolvableError(
                "recaptcha v2 userverify refused transcript",
                context={
                    "status": verify_resp.status,
                    "transcript_len": len(transcript),
                    "body_head": verify_resp.text[:120],
                },
            )
        return SolverResult(
            token=final_token,
            text=transcript,
            solve_mode=SolveMode.HTTP,
            extra={"audio_bytes": len(audio_resp.body)},
        )

    async def _transcribe(self, mp3: bytes, *, task: Task, ctx: SolverContext) -> str:
        """Hand the MP3 to the registry-resolved audio solver."""
        audio_cls = _pick_audio_solver(ctx)
        # Build a synthetic Task carrying the mp3 bytes under payload — the
        # audio solver's contract is `payload['audio']: bytes`.
        audio_task = Task(
            id=task.id,
            type=CaptchaType.AUDIO,
            payload={"audio": mp3, "format": "mp3", "language": task.language or "en"},
            priority=task.priority,
            preferred_mode=SolveMode.HTTP,
            site_url=task.site_url,
            language=task.language,
            created_at=task.created_at,
            deadline_ts=task.deadline_ts,
            client_id=task.client_id,
        )
        solver = audio_cls()
        try:
            await solver.prepare(ctx)
            result = await solver.solve_http(audio_task, ctx)
        finally:
            try:
                await solver.shutdown()
            except Exception as exc:  # noqa: BLE001
                _log.warning("audio_solver.shutdown_failed", error=str(exc))
        text = (result.text or "").strip()
        return text


@register_solver
class RecaptchaV2AudioSolver(_RecaptchaV2Base):
    """v2 checkbox + invisible + callback — all three take this path."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="recaptcha_v2_audio",
        types=(
            CaptchaType.RECAPTCHA_V2,
            CaptchaType.RECAPTCHA_V2_INVISIBLE,
            CaptchaType.RECAPTCHA_V2_CALLBACK,
        ),
        http=True,
        browser=False,
        needs_js_runtime=False,
        experimental=False,
        version="0.1.0",
    )


@register_solver
class RecaptchaV2EnterpriseSolver(_RecaptchaV2Base):
    """Enterprise variant — same audio pipeline, enterprise-prefixed URLs."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="recaptcha_v2_enterprise_audio",
        types=(CaptchaType.RECAPTCHA_V2_ENTERPRISE,),
        http=True,
        browser=False,
        needs_js_runtime=False,
        experimental=False,
        version="0.1.0",
    )

    _ANCHOR = ANCHOR_URL_ENT
    _RELOAD = RELOAD_URL_ENT
    _PAYLOAD = PAYLOAD_URL_ENT
    _USERVERIFY = USERVERIFY_URL_ENT


# Re-exported for test harnesses / fixture callers that want to poke at the
# response parsers without going through the solver.
__parsers__: dict[str, Any] = {
    "extract_anchor_token": _extract_anchor_token,
    "extract_reload_field": _extract_reload_field,
}

# FILL: endpoint path versions change when Google rolls grecaptcha — bump
# `API_VERSION` and re-dump the reload response shape. The reload body
# gained a `ds` field around build DKw2...; older builds 400 on it, newer
# ones require it. Keep the parser tolerant of either.
