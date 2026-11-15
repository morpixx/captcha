"""OCR engine layer: result parsing and the config-vs-inference error split."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from captcha.domain.errors import ConfigError
from captcha.solvers.ocr import _engines


def test_classic_paddle_shape_is_parsed():
    page = [
        [[[0, 0], [10, 10]], ("Hello", 0.99)],
        [[[0, 10], [10, 20]], ("World", 0.90)],
    ]
    out: list[str] = []
    _engines._paddle_result_text([page], out)
    assert out == ["Hello", "World"]


def test_paddle_3x_object_shape_is_parsed():
    node = SimpleNamespace(rec_texts=["A", "B"])
    out: list[str] = []
    _engines._paddle_result_text([node], out)
    assert out == ["A", "B"]


def test_paddle_3x_dict_shape_is_parsed():
    out: list[str] = []
    _engines._paddle_result_text([{"rec_texts": ["C"]}], out)
    assert out == ["C"]


def test_get_paddle_without_package_is_config_error():
    with patch.dict(sys.modules, {"paddleocr": None}), pytest.raises(ConfigError):
        _engines.get_paddle("en")


def test_trocr_local_path_must_exist(tmp_path: Path):
    with pytest.raises(ConfigError):
        _engines.get_trocr(model_path=str(tmp_path / "missing"))


def test_trocr_local_path_must_be_a_hf_directory(tmp_path: Path):
    with pytest.raises(ConfigError) as excinfo:
        _engines.get_trocr(model_path=str(tmp_path))
    assert "config.json" in str(excinfo.value)


def test_paddle_model_dir_must_exist(tmp_path: Path):
    with pytest.raises(ConfigError):
        _engines.get_paddle("en", dirs={"rec_model_dir": str(tmp_path / "nope")})
