"""audio_captcha - Whisper-based ASR for audio-challenge captchas.

Payload: {body: base64 audio, format: mp3|wav|ogg|flac|m4a, model?, language?}.

Whisper is optional and local model paths are honoured when present. Missing
backend or missing local weights is ERR_CONFIG (terminal, the image/volume is
wrong); a failed transcription is ERR_MODEL_INFERENCE (retryable). An empty
transcript is a failed task, not an empty answer.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, ClassVar

from captcha.config import get_settings
from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.solvers.audio._engines import transcribe
from captcha.solvers.base import Solver, SolverContext, SolverInfo

_SAFE_FMT = re.compile(r"^[a-z0-9]{1,8}$")
_MAX_B64_CHARS = 24_000_000  # ~16 MiB decoded; a 30s challenge is ~0.5 MiB
_MAX_AUDIO_BYTES = 16 * 1024 * 1024


def _b64decode(body: Any) -> bytes:
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    if not isinstance(body, str):
        raise UnsolvableError("audio_captcha: body must be a base64 string", context={"got": type(body).__name__})
    text = body.strip()
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    text = "".join(text.split())
    if not text:
        raise UnsolvableError("audio_captcha: empty audio body")
    if len(text) > _MAX_B64_CHARS:
        raise UnsolvableError(
            "audio_captcha: audio body too large",
            context={"base64_chars": len(text), "cap": _MAX_B64_CHARS},
        )
    padded = text + "=" * ((-len(text)) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        pass
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise UnsolvableError("audio_captcha: bad base64", context={"error": str(exc)}) from exc


def _decode_audio(payload: dict[str, Any]) -> tuple[bytes, str]:
    body = payload.get("body") or payload.get("audio") or payload.get("base64")
    if not body:
        raise UnsolvableError("audio_captcha: payload missing 'body' (base64 audio)")
    raw = _b64decode(body)
    if not raw:
        raise UnsolvableError("audio_captcha: empty audio body")
    if len(raw) > _MAX_AUDIO_BYTES:
        raise UnsolvableError(
            "audio_captcha: audio body too large",
            context={"bytes": len(raw), "cap": _MAX_AUDIO_BYTES},
        )
    fmt = str(payload.get("format") or "mp3").strip().lower().lstrip(".")
    if not _SAFE_FMT.match(fmt):
        fmt = "mp3"
    return raw, fmt


def _language(payload: dict[str, Any], task: Task) -> str | None:
    value = payload.get("language") or task.language
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


class AudioCaptchaSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="audio_captcha",
        types=(CaptchaType.AUDIO,),
        http=True,
        version="0.2.0",
    )

    def __init__(self) -> None:
        super().__init__()
        self._settings: Any = None

    async def prepare(self, ctx: SolverContext) -> None:
        self._settings = getattr(ctx, "config", None) or get_settings()

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.perf_counter()
        settings = self._settings or getattr(ctx, "config", None) or get_settings()

        payload = task.payload or {}
        raw, fmt = _decode_audio(payload)
        model_name = str(payload.get("model") or "small")
        device = str(payload.get("device") or getattr(settings, "worker_cv_device", "") or "cpu")
        language = _language(payload, task)

        text = await self._transcribe_file(raw, fmt, model_name, device, language)

        text = text.strip()
        if not text:
            raise ModelInferenceError(
                "audio_captcha: Whisper returned an empty transcription",
                context={"model": model_name, "device": device},
            )

        return SolverResult(
            text=text,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            extra={"model": model_name, "device": device, "language": language},
        )

    async def _transcribe_file(
        self,
        raw: bytes,
        fmt: str,
        model_name: str,
        device: str,
        language: str | None,
    ) -> str:
        fd, name = tempfile.mkstemp(suffix=f".{fmt}", prefix="captcha_audio_")
        os.close(fd)
        tmp_path = Path(name)
        try:
            try:
                tmp_path.write_bytes(raw)
            except OSError as exc:
                raise ModelInferenceError(
                    f"audio_captcha: cannot stage the audio file: {exc}",
                    context={"path": str(tmp_path)},
                ) from exc
            try:
                return await asyncio.to_thread(
                    transcribe,
                    str(tmp_path),
                    model_name=model_name,
                    device=device,
                    language=language,
                )
            except (ConfigError, ModelInferenceError):
                raise
            except Exception as exc:
                raise ModelInferenceError(
                    f"audio_captcha: transcription crashed: {exc}",
                    context={"model": model_name, "error_type": type(exc).__name__},
                ) from exc
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
