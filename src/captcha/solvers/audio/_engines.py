"""Lazy Whisper engine singletons. faster-whisper first, openai-whisper second.

Both are optional: the base worker image ships neither. Import happens on first
use and stays behind guards.

Error split:
  * no backend installed            -> ConfigError (ERR_CONFIG, terminal)
  * local model path does not exist -> ConfigError (operator fixes the volume)
  * model load / transcribe failed  -> ModelInferenceError (retryable)
"""

from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path
from typing import Any

from captcha.domain.errors import ConfigError, ModelInferenceError

_FW_LOCK = threading.Lock()
_OW_LOCK = threading.Lock()
# key = (model_name, device, compute_type) -> handle
_faster_cache: dict[tuple[str, str, str], Any] = {}
_openai_cache: dict[tuple[str, str], Any] = {}

_FW_HINT = (
    "faster-whisper is not installed in this worker image (pip package 'faster-whisper', optional). "
    "Run the audio lane on an image that has it."
)
_OW_HINT = (
    "No Whisper backend is installed in this worker image (needs 'faster-whisper' or 'openai-whisper'). "
    "Run the audio lane on an image that has one."
)


def _compute_type_for(device: str) -> str:
    return "float16" if device.startswith("cuda") else "int8"


def _looks_like_path(model_name: str) -> bool:
    return (
        model_name.startswith((".", "/", "\\"))
        or os.sep in model_name
        or (len(model_name) > 1 and model_name[1] == ":")
    )


def _checked_model(model_name: str) -> str:
    """Local Whisper paths must exist; bare names are hub/model ids."""
    if not _looks_like_path(model_name):
        return model_name
    path = Path(model_name).expanduser()
    if not path.exists():
        raise ConfigError(
            f"audio: local Whisper model not found: {path}",
            context={"model": model_name},
        )
    return str(path)


def have_faster_whisper() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def have_openai_whisper() -> bool:
    return importlib.util.find_spec("whisper") is not None


def pick_backend() -> str | None:
    if have_faster_whisper():
        return "faster-whisper"
    if have_openai_whisper():
        return "whisper"
    return None


def get_faster_whisper(model_name: str, device: str) -> Any:
    compute = _compute_type_for(device)
    model_name = _checked_model(model_name)
    key = (model_name, device, compute)
    with _FW_LOCK:
        cached = _faster_cache.get(key)
        if cached is not None:
            return cached
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except Exception as exc:
            raise ConfigError(_FW_HINT, context={"import_error": repr(exc)}) from exc
        try:
            model = WhisperModel(model_name, device=device, compute_type=compute)
        except Exception as exc:
            raise ModelInferenceError(
                f"failed to load faster-whisper model '{model_name}' on {device}: {exc}",
                context={"model": model_name, "device": device, "compute_type": compute},
            ) from exc
        _faster_cache[key] = model
        return model


def get_openai_whisper(model_name: str, device: str) -> Any:
    model_name = _checked_model(model_name)
    key = (model_name, device)
    with _OW_LOCK:
        cached = _openai_cache.get(key)
        if cached is not None:
            return cached
        try:
            import whisper  # type: ignore[import-not-found]
        except Exception as exc:
            raise ConfigError(_OW_HINT, context={"import_error": repr(exc)}) from exc
        try:
            model = whisper.load_model(model_name, device=device)
        except Exception as exc:
            raise ModelInferenceError(
                f"failed to load openai-whisper model '{model_name}' on {device}: {exc}",
                context={"model": model_name, "device": device},
            ) from exc
        _openai_cache[key] = model
        return model


def transcribe(
    audio_path: str,
    *,
    model_name: str,
    device: str,
    language: str | None,
) -> str:
    """Transcribe a WAV/MP3/FLAC file to text with whichever backend is installed."""
    backend = pick_backend()
    if backend is None:
        raise ConfigError(_OW_HINT, context={"model": model_name})

    if backend == "faster-whisper":
        model = get_faster_whisper(model_name, device)
        try:
            segments, _info = model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=False,
            )
            return " ".join(segment.text for segment in segments).strip()
        except Exception as exc:
            raise ModelInferenceError(
                f"faster-whisper transcribe failed: {exc}",
                context={"model": model_name, "device": device},
            ) from exc

    model = get_openai_whisper(model_name, device)
    try:
        out = model.transcribe(audio_path, language=language, fp16=device.startswith("cuda"))
    except Exception as exc:
        raise ModelInferenceError(
            f"openai-whisper transcribe failed: {exc}",
            context={"model": model_name, "device": device},
        ) from exc
    return str(out.get("text", "")).strip()
