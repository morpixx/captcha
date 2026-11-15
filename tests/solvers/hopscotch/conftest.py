"""Fixtures for the hopscotch solver tests: a valid artifact on disk, no torch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from captcha.solvers.hopscotch.artifact import (
    CONFIG_FILE,
    FEATURES,
    PREPROCESSING,
    RELEASE_FILE,
    WEIGHTS_FILE,
)

#: Median-ish rejector that turns ~every feature vector into a valid score.
GOOD_MEAN = [0.6, 0.4, 0.5, 0.9, 0.5, 0.8, 0.7, 0.5]
GOOD_SCALE = [0.2, 0.3, 0.3, 0.2, 0.15, 0.1, 0.1, 0.5]


def config_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "format": "hopscotch-v1-config",
        "model_name": "hopscotch-v1",
        "version": "v1-test",
        "preprocessing": PREPROCESSING,
        "model": {
            "architecture": "spatial",
            "hidden_dim": 128,
            "slots": 8,
            "image_size": 384,
            "crop_size": 128,
            "dropout": 0.1,
            "encoder": "models/dinov3-b16",
        },
        "train": {"encoder_chunk": 32, "amp": True},
        "backbone_config": {"hidden_size": 768, "patch_size": 16, "num_hidden_layers": 12},
        "input": {
            "image_height": 400,
            "card_width": 200,
            "card_height": 200,
            "hint_box": [0, 200, 135, 400],
            "frames": [5, 8],
            "index_base": 1,
        },
        "checkpoint_epoch": 39,
        "source": {"run": "unit-test"},
    }
    payload.update(overrides)
    return payload


def release_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "format": "captcha-hopscotch-release-v1",
        "model_name": "hopscotch-v1",
        "version": "v1-test",
        "status": "ready",
        "preprocessing": PREPROCESSING,
        "features": list(FEATURES),
        "guards": {"presence": 0.5},
        "threshold": 0.5,
        "mean": GOOD_MEAN,
        "scale": GOOD_SCALE,
        "weights": [0.5] * 8,
        "bias": 2.0,
        "metrics": {"gold_val": {"answer_accuracy": 0.9}},
        "split_sha256": "a" * 64,
        "created_at": "2026-11-15T00:00:00+00:00",
    }
    payload.update(overrides)
    return payload


def write_artifact(
    directory: Path,
    *,
    config: dict[str, Any] | None = None,
    release: dict[str, Any] | None = None,
    weights: bytes = b"fake-safetensors",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / CONFIG_FILE).write_text(json.dumps(config or config_payload()), encoding="utf-8")
    (directory / RELEASE_FILE).write_text(json.dumps(release or release_payload()), encoding="utf-8")
    (directory / WEIGHTS_FILE).write_bytes(weights)
    return directory


@pytest.fixture
def artifact_dir(tmp_path: Path) -> Path:
    return write_artifact(tmp_path / "artifact")
