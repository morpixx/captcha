"""image_to_text: mocked engine paths, no Paddle/TrOCR runtime and no network."""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from captcha.domain import CaptchaType
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.domain.task import Task, new_task_id
from captcha.solvers.base import SolverContext
from captcha.solvers.ocr import image_to_text as module
from captcha.solvers.ocr.image_to_text import ImageToTextSolver


def _png_b64(size: tuple[int, int] = (64, 32)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _task(payload: dict) -> Task:
    return Task(id=new_task_id(), type=CaptchaType.IMAGE_TO_TEXT, payload=payload)


def _solve(payload: dict, config: object | None = None):
    return asyncio.run(ImageToTextSolver().solve_http(_task(payload), SolverContext(config=config)))


def test_paddle_engine_returns_lowercased_text():
    with patch.object(module, "paddle_read", return_value="AB12") as read:
        result = _solve({"body": _png_b64(), "engine": "paddle"})
    assert result.text == "ab12"
    assert result.extra["engine"] == "paddle"
    read.assert_called_once()


def test_case_sensitive_keeps_case():
    with patch.object(module, "paddle_read", return_value="AbC!"):
        result = _solve({"body": _png_b64(), "engine": "paddle", "case": True})
    assert result.text == "AbC!"


def test_trocr_gets_device_from_config():
    config = SimpleNamespace(worker_cv_device="cuda:0")
    with patch.object(module, "trocr_read", return_value="7x9") as read:
        result = _solve({"body": _png_b64(), "engine": "trocr"}, config=config)
    assert result.text == "7x9"
    assert read.call_args.args[1] == "cuda:0"


def test_auto_picks_installed_engine():
    with (
        patch.object(module, "available_engines", return_value=["trocr"]),
        patch.object(module, "trocr_read", return_value="auto") as read,
    ):
        result = _solve({"body": _png_b64()})
    assert result.text == "auto"
    assert result.extra["engine"] == "trocr"
    read.assert_called_once()


def test_auto_without_any_engine_is_config_error():
    with (
        patch.object(module, "available_engines", return_value=[]),
        pytest.raises(ConfigError),
    ):
        _solve({"body": _png_b64()})


def test_unknown_engine_is_config_error():
    with pytest.raises(ConfigError):
        _solve({"body": _png_b64(), "engine": "tesseract"})


def test_missing_body_is_unsolvable():
    with pytest.raises(UnsolvableError):
        _solve({"engine": "paddle"})


def test_bad_base64_is_unsolvable():
    with pytest.raises(UnsolvableError):
        _solve({"body": "@@@not-base64@@@", "engine": "paddle"})


def test_unreadable_image_is_unsolvable():
    body = base64.b64encode(b"this is not an image").decode()
    with pytest.raises(UnsolvableError):
        _solve({"body": body, "engine": "paddle"})


def test_data_uri_prefix_is_stripped():
    with patch.object(module, "paddle_read", return_value="ok"):
        result = _solve({"body": "data:image/png;base64," + _png_b64(), "engine": "paddle"})
    assert result.text == "ok"


def test_bytes_body_is_accepted():
    raw = base64.b64decode(_png_b64())
    with patch.object(module, "paddle_read", return_value="ok"):
        result = _solve({"body": raw, "engine": "paddle"})
    assert result.text == "ok"


def test_dimension_cap_is_enforced(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(module, "_MAX_PIXELS", 100)
    with pytest.raises(UnsolvableError) as excinfo:
        _solve({"body": _png_b64((64, 32)), "engine": "paddle"})
    assert "dimensions" in str(excinfo.value)


def test_empty_ocr_output_is_model_error_not_an_answer():
    with (
        patch.object(module, "paddle_read", return_value="   "),
        pytest.raises(ModelInferenceError),
    ):
        _solve({"body": _png_b64(), "engine": "paddle"})


def test_engine_config_error_propagates():
    with (
        patch.object(module, "paddle_read", side_effect=ConfigError("no paddle")),
        pytest.raises(ConfigError),
    ):
        _solve({"body": _png_b64(), "engine": "paddle"})


def test_engine_crash_is_wrapped_as_model_error():
    with (
        patch.object(module, "paddle_read", side_effect=RuntimeError("segfault-ish")),
        pytest.raises(ModelInferenceError) as excinfo,
    ):
        _solve({"body": _png_b64(), "engine": "paddle"})
    assert excinfo.value.context["error_type"] == "RuntimeError"


def test_local_paddle_dirs_are_forwarded(tmp_path: Path):
    with patch.object(module, "paddle_read", return_value="ok") as read:
        _solve(
            {
                "body": _png_b64(),
                "engine": "paddle",
                "rec_model_dir": str(tmp_path),
            }
        )
    assert read.call_args.kwargs["dirs"] == {"rec_model_dir": str(tmp_path)}
