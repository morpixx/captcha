"""Audio transcription resolution for the v2 audio path.

Whisper is not imported here. The worker injects one of:

    ctx.extra["audio_transcriber"]  sync/async callable (mp3, *, task, ctx)
    ctx.extra["transcribe"]         alias
    ctx.extra["whisper"] / "asr"    callable or object with .transcribe(mp3, ...)
    ctx.extra["audio_solver"]       Solver instance/class for CaptchaType.AUDIO
    ctx.extra["solver_registry"]    registry with .pick(CaptchaType.AUDIO)

No injection is ERR_CONFIG, not a silent retry: shipping an untranscribed MP3
to userverify can only burn the challenge.
"""

from __future__ import annotations

import inspect
from typing import Any

import msgspec
import structlog

from captcha.domain import CaptchaType, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ConfigError
from captcha.solvers import Solver, SolverContext

_log = structlog.get_logger(__name__)


async def transcribe(mp3: bytes, *, task: Task, ctx: SolverContext) -> str:
    """Return the transcript for `mp3` using the injected backend."""
    injected = _injected_transcriber(ctx)
    if injected is not None:
        return await _call_transcriber(injected, mp3, task=task, ctx=ctx)

    solver = _audio_solver(ctx)
    audio_task = msgspec.structs.replace(
        task,
        type=CaptchaType.AUDIO,
        payload={"audio": mp3, "format": "mp3", "language": task.language or "en"},
        preferred_mode=SolveMode.HTTP,
    )
    prepared = False
    try:
        await solver.prepare(ctx)
        prepared = True
        result = await solver.solve_http(audio_task, ctx)
    finally:
        if prepared:
            try:
                await solver.shutdown()
            except Exception as exc:
                _log.warning("audio_solver.shutdown_failed", error=str(exc))
    return (result.text or "").strip()


def _injected_transcriber(ctx: SolverContext) -> Any:
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    for key in ("audio_transcriber", "transcribe", "whisper", "asr"):
        candidate = _callable(candidate=extra.get(key))
        if candidate is not None:
            return candidate
    candidate = _callable(candidate=extra.get("audio_solver"))
    if candidate is not None and not _is_solver_class(extra.get("audio_solver")):
        return candidate
    return None


def _callable(*, candidate: Any) -> Any:
    if callable(candidate):
        return candidate
    method = getattr(candidate, "transcribe", None)
    return method if callable(method) else None


def _is_solver_class(candidate: Any) -> bool:
    return isinstance(candidate, type) and issubclass(candidate, Solver)


def _audio_solver(ctx: SolverContext) -> Solver:
    extra = ctx.extra if isinstance(ctx.extra, dict) else {}
    injected = extra.get("audio_solver")
    if isinstance(injected, Solver):
        return injected
    if _is_solver_class(injected):
        return injected()
    registry = extra.get("solver_registry")
    if registry is None:
        raise ConfigError(
            "reCAPTCHA v2 audio path needs ctx.extra['audio_transcriber'] "
            "or ctx.extra['solver_registry'] (Whisper is not imported here)",
            context={"needs": ["audio_transcriber", "solver_registry"]},
        )
    solver_cls = registry.pick(CaptchaType.AUDIO)
    if solver_cls is None:
        raise ConfigError(
            "no solver registered for CaptchaType.AUDIO",
            context={"type": CaptchaType.AUDIO.value},
        )
    return solver_cls()


async def _call_transcriber(fn: Any, mp3: bytes, *, task: Task, ctx: SolverContext) -> str:
    kwargs: dict[str, Any] = {}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if "task" in params or "ctx" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kwargs = {"task": task, "ctx": ctx}
    result = fn(mp3, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return str(result or "").strip()
