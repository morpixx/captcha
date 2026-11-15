"""audio_captcha: Whisper is mocked; no model download, no network."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from captcha.domain import CaptchaType
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.domain.task import Task, new_task_id
from captcha.solvers.audio import _engines
from captcha.solvers.audio import audio_captcha as module
from captcha.solvers.audio.audio_captcha import AudioCaptchaSolver
from captcha.solvers.base import SolverContext

AUDIO_B64 = base64.b64encode(b"ID3-fake-mp3-bytes").decode()


def _task(payload: dict, *, language: str | None = None) -> Task:
    return Task(id=new_task_id(), type=CaptchaType.AUDIO, payload=payload, language=language)


def _solve(payload: dict, *, config: object | None = None, language: str | None = None):
    ctx = SolverContext(config=config or SimpleNamespace(worker_cv_device="cpu"))
    return asyncio.run(AudioCaptchaSolver().solve_http(_task(payload, language=language), ctx))


def test_missing_body_is_unsolvable():
    with pytest.raises(UnsolvableError):
        _solve({})


def test_bad_base64_is_unsolvable():
    with pytest.raises(UnsolvableError):
        _solve({"body": "@@@not-base64@@@"})


def test_empty_body_is_unsolvable():
    with pytest.raises(UnsolvableError):
        _solve({"body": "   "})


def test_format_is_normalized():
    assert module._decode_audio({"body": AUDIO_B64, "format": "WAV"})[1] == "wav"
    assert module._decode_audio({"body": AUDIO_B64, "format": "../evil"})[1] == "mp3"
    assert module._decode_audio({"body": AUDIO_B64})[1] == "mp3"


def test_oversized_body_is_unsolvable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "_MAX_B64_CHARS", 8)
    with pytest.raises(UnsolvableError):
        _solve({"body": AUDIO_B64})


def test_successful_transcription_and_temp_cleanup():
    captured: dict[str, str] = {}

    def fake_transcribe(path: str, *, model_name: str, device: str, language: str | None) -> str:
        captured["path"] = path
        assert Path(path).exists()
        assert model_name == "small"
        assert device == "cpu"
        assert language == "en"
        return "  hello world  "

    with patch.object(module, "transcribe", side_effect=fake_transcribe):
        result = _solve({"body": AUDIO_B64}, language="en")

    assert result.text == "hello world"
    assert result.extra == {"model": "small", "device": "cpu", "language": "en"}
    assert not Path(captured["path"]).exists()


def test_device_comes_from_settings():
    seen: dict[str, str] = {}

    def fake_transcribe(path: str, *, model_name: str, device: str, language: str | None) -> str:
        seen["device"] = device
        return "x"

    config = SimpleNamespace(worker_cv_device="cuda:0")
    with patch.object(module, "transcribe", side_effect=fake_transcribe):
        _solve({"body": AUDIO_B64}, config=config)
    assert seen["device"] == "cuda:0"


def test_payload_language_overrides_task_language():
    seen: dict[str, str | None] = {}

    def fake_transcribe(path: str, *, model_name: str, device: str, language: str | None) -> str:
        seen["language"] = language
        return "x"

    with patch.object(module, "transcribe", side_effect=fake_transcribe):
        _solve({"body": AUDIO_B64, "language": " de "}, language="en")
    assert seen["language"] == "de"


def test_empty_transcript_is_model_error():
    with patch.object(module, "transcribe", return_value="   "), pytest.raises(ModelInferenceError):
        _solve({"body": AUDIO_B64})


def test_config_error_propagates():
    with patch.object(module, "transcribe", side_effect=ConfigError("no whisper")), pytest.raises(ConfigError):
        _solve({"body": AUDIO_B64})


def test_backend_crash_is_wrapped():
    with (
        patch.object(module, "transcribe", side_effect=RuntimeError("boom")),
        pytest.raises(ModelInferenceError) as excinfo,
    ):
        _solve({"body": AUDIO_B64})
    assert excinfo.value.context["error_type"] == "RuntimeError"


def test_transcribe_without_any_backend_is_config_error():
    with patch.object(_engines, "pick_backend", return_value=None), pytest.raises(ConfigError):
        _engines.transcribe("x.wav", model_name="small", device="cpu", language=None)


def test_faster_whisper_import_guard():
    with patch.dict(sys.modules, {"faster_whisper": None}), pytest.raises(ConfigError):
        _engines.get_faster_whisper("tiny", "cpu")


def test_missing_local_model_path_is_config_error():
    with pytest.raises(ConfigError):
        _engines.get_faster_whisper("./no/such/whisper-model", "cpu")


def test_openai_whisper_import_guard():
    with patch.dict(sys.modules, {"whisper": None}), pytest.raises(ConfigError):
        _engines.get_openai_whisper("tiny", "cpu")
