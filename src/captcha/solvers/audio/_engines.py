"""Lazy Whisper engine singletons. Prefer faster-whisper; fall back to openai-whisper."""

from __future__ import annotations

import threading
from typing import Any

from captcha.domain.errors import ModelInferenceError

_FW_LOCK = threading.Lock()
_OW_LOCK = threading.Lock()
# key = (model_name, device, compute_type) -> handle
_faster_cache: dict[tuple[str, str, str], Any] = {}
_openai_cache: dict[tuple[str, str], Any] = {}

_FW_HINT = (
    "faster-whisper is not installed. Install the engine extra: `uv pip install -e solvers/audio[faster-whisper]`."
)
_OW_HINT = (
    "openai-whisper is not installed. Install one of the engine extras: "
    "`uv pip install -e solvers/audio[faster-whisper]` (preferred) or "
    "`uv pip install -e solvers/audio[whisper]`."
)


def _compute_type_for(device: str) -> str:
    return "float16" if device.startswith("cuda") else "int8"


def get_faster_whisper(model_name: str, device: str) -> Any:
    compute = _compute_type_for(device)
    key = (model_name, device, compute)
    with _FW_LOCK:
        cached = _faster_cache.get(key)
        if cached is not None:
            return cached
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except Exception as e:  # pragma: no cover
            raise ModelInferenceError(_FW_HINT, context={"import_error": repr(e)}) from e
        try:
            model = WhisperModel(model_name, device=device, compute_type=compute)
        except Exception as e:
            raise ModelInferenceError(
                f"failed to load faster-whisper model '{model_name}' on {device}: {e}",
                context={"model": model_name, "device": device, "compute_type": compute},
            ) from e
        _faster_cache[key] = model
        return model


def get_openai_whisper(model_name: str, device: str) -> Any:
    key = (model_name, device)
    with _OW_LOCK:
        cached = _openai_cache.get(key)
        if cached is not None:
            return cached
        try:
            import whisper  # type: ignore[import-not-found]
        except Exception as e:  # pragma: no cover
            raise ModelInferenceError(_OW_HINT, context={"import_error": repr(e)}) from e
        try:
            model = whisper.load_model(model_name, device=device)
        except Exception as e:
            raise ModelInferenceError(
                f"failed to load openai-whisper model '{model_name}' on {device}: {e}",
                context={"model": model_name, "device": device},
            ) from e
        _openai_cache[key] = model
        return model


def have_faster_whisper() -> bool:
    try:
        import faster_whisper  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return False
    return True


def have_openai_whisper() -> bool:
    try:
        import whisper  # type: ignore[import-not-found]  # noqa: F401
    except Exception:
        return False
    return True


def transcribe(
    audio_path: str,
    *,
    model_name: str,
    device: str,
    language: str | None,
) -> str:
    """Transcribe a WAV/MP3/FLAC file to text. Picks faster-whisper if available."""
    if have_faster_whisper():
        model = get_faster_whisper(model_name, device)
        try:
            segments, _info = model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=False,
            )
            return " ".join(seg.text for seg in segments).strip()
        except Exception as e:
            raise ModelInferenceError(
                f"faster-whisper transcribe failed: {e}",
                context={"model": model_name, "device": device},
            ) from e

    if have_openai_whisper():
        model = get_openai_whisper(model_name, device)
        try:
            out = model.transcribe(audio_path, language=language, fp16=device.startswith("cuda"))
            return str(out.get("text", "")).strip()
        except Exception as e:
            raise ModelInferenceError(
                f"openai-whisper transcribe failed: {e}",
                context={"model": model_name, "device": device},
            ) from e

    raise ModelInferenceError(_OW_HINT)
