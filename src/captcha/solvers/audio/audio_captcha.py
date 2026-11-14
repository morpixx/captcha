"""audio_captcha — Whisper-based ASR for audio-challenge captchas."""

from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
import time
from pathlib import Path
from typing import ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.config import Settings, get_settings
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ModelInferenceError, UnsolvableError
from captcha.solvers.base import Solver, SolverContext, SolverInfo

from captcha.solvers.audio._engines import transcribe

_SAFE_FMT = re.compile(r"^[a-z0-9]{1,8}$")


def _decode_audio(payload: dict) -> tuple[bytes, str]:
    body = payload.get("body") or payload.get("audio") or payload.get("base64")
    if not body:
        raise UnsolvableError("audio_captcha: payload missing 'body' (base64 audio)")
    if isinstance(body, str) and body.startswith("data:"):
        _, _, body = body.partition(",")
    try:
        raw = base64.b64decode(body, validate=False)
    except (binascii.Error, ValueError) as e:
        raise UnsolvableError(f"audio_captcha: bad base64 ({e})") from e
    fmt = str(payload.get("format", "mp3")).lower()
    if not _SAFE_FMT.match(fmt):
        fmt = "mp3"
    return raw, fmt


class AudioCaptchaSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="audio_captcha",
        types=(CaptchaType.AUDIO,),
        http=True,
        browser=False,
        needs_gpu=False,
    )

    def __init__(self) -> None:
        super().__init__()
        self._settings: Settings | None = None

    async def prepare(self, ctx: SolverContext) -> None:
        self._settings = ctx.config if isinstance(ctx.config, Settings) else get_settings()

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.perf_counter()
        settings = self._settings or (ctx.config if isinstance(ctx.config, Settings) else get_settings())

        payload = task.payload or {}
        raw, fmt = _decode_audio(payload)

        model_name = str(payload.get("model") or "small")
        device = str(payload.get("device") or settings.worker_cv_device or "cpu")
        language = payload.get("language") or task.language  # None → auto-detect

        tmp_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(suffix=f".{fmt}", prefix="captcha_audio_")
            tmp_path = Path(name)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(raw)
            except Exception:
                os.close(fd)
                raise
            text = transcribe(
                str(tmp_path),
                model_name=model_name,
                device=device,
                language=language,
            )
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        text = text.strip()
        if not text:
            raise ModelInferenceError(
                "audio_captcha: Whisper returned empty transcription",
                context={"model": model_name, "device": device},
            )

        return SolverResult(
            text=text,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            extra={"model": model_name, "device": device, "language": language},
        )
