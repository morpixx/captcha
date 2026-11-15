"""Artifact contract tests: what the worker is willing to load and answer with.

These are the rules that keep a broken export from reaching a customer: wrong
feature order, an uncalibrated release, a weights/config mismatch, a payload the
model was never trained on.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from captcha.domain.catalog import SolutionKind, lane_for, solution_kind
from captcha.domain.enums import CaptchaType
from captcha.domain.errors import ConfigError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.solvers.hopscotch.artifact import (
    CONFIG_FILE,
    FEATURES,
    RELEASE_FILE,
    WEIGHTS_FILE,
    load_artifact,
)
from captcha.solvers.hopscotch.calibration import Observation, decide
from captcha.solvers.hopscotch.payload import foot_to_pixels, parse_payload
from conftest import config_payload, release_payload, write_artifact


def _wave(frames: int, *, height: int = 400) -> str:
    image = Image.new("RGB", (frames * 200, height), (30, 60, 90))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


# ---- catalog wiring ---------------------------------------------------------


def test_type_is_cv_lane_with_own_solution_kind() -> None:
    assert lane_for(CaptchaType.HOPSCOTCH) == "cv"
    assert solution_kind(CaptchaType.HOPSCOTCH) is SolutionKind.HOPSCOTCH


def test_solution_projects_index_and_coordinates() -> None:
    out = build_solution(
        CaptchaType.HOPSCOTCH,
        {"extra": {"index": 3, "coordinates": [420.0, 100.0]}, "coords": [(420.0, 100.0)]},
    )
    assert out == {"index": 3, "coordinates": [420.0, 100.0]}


# ---- loading ----------------------------------------------------------------


def test_valid_artifact_loads(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    assert artifact.config.input.frames == (5, 8)
    assert artifact.config.input.index_base == 1
    assert artifact.release.ready
    assert artifact.weights.name == WEIGHTS_FILE


def test_missing_directory_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_artifact(tmp_path / "nope")


def test_missing_weights_is_config_error(artifact_dir: Path) -> None:
    (artifact_dir / WEIGHTS_FILE).unlink()
    with pytest.raises(ConfigError):
        load_artifact(artifact_dir)


def test_empty_weights_is_config_error(artifact_dir: Path) -> None:
    (artifact_dir / WEIGHTS_FILE).write_bytes(b"")
    with pytest.raises(ConfigError):
        load_artifact(artifact_dir)


def test_preprocessing_mismatch_is_config_error(artifact_dir: Path) -> None:
    (artifact_dir / CONFIG_FILE).write_text(
        json.dumps(config_payload(preprocessing="rgb-v2")) , encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_artifact(artifact_dir)


def test_feature_order_mismatch_is_rejected(artifact_dir: Path) -> None:
    payload = release_payload(features=list(reversed(FEATURES)))
    (artifact_dir / RELEASE_FILE).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_artifact(artifact_dir)


def test_ready_release_requires_threshold(artifact_dir: Path) -> None:
    (artifact_dir / RELEASE_FILE).write_text(
        json.dumps(release_payload(threshold=None)), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_artifact(artifact_dir)


def test_scale_must_be_positive(artifact_dir: Path) -> None:
    scale = list(release_payload()["scale"])
    scale[0] = 0.0
    (artifact_dir / RELEASE_FILE).write_text(
        json.dumps(release_payload(scale=scale)), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_artifact(artifact_dir)


def test_index_base_must_be_one(artifact_dir: Path) -> None:
    payload = config_payload()
    payload["input"]["index_base"] = 0
    (artifact_dir / CONFIG_FILE).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_artifact(artifact_dir)


# ---- calibration ------------------------------------------------------------


def _observation(
    features: tuple[float, ...] | None, *, valid: bool = True, reason: str = "valid"
) -> Observation:
    return Observation(index=2, foot=(0.5, 0.5), features=features, valid=valid, reason=reason)


def test_uncalibrated_release_abstains(artifact_dir: Path) -> None:
    artifact = load_artifact(write_artifact(artifact_dir, release=release_payload(status="not_ready", threshold=None)))
    decision = decide(_observation(tuple(GOOD_INPUT)), artifact.release)
    assert decision.abstained and decision.reason == "uncalibrated"


def test_high_score_is_accepted(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    decision = decide(_observation(tuple(GOOD_INPUT)), artifact.release)
    assert decision.accepted
    assert decision.score is not None and decision.score >= 0.5


def test_low_score_abstains(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    decision = decide(_observation(tuple(BAD_INPUT)), artifact.release)
    assert decision.abstained
    assert decision.reason == "low_correctness_score"


def test_structural_reason_is_preserved(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    decision = decide(
        _observation(None, valid=False, reason="invalid_geometry"), artifact.release
    )
    assert not decision.accepted
    assert decision.reason == "invalid_geometry"


def test_missing_features_abstains(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    decision = decide(_observation(None), artifact.release)
    assert decision.reason == "missing_features"


GOOD_INPUT = [0.6, 0.4, 0.5, 0.9, 0.5, 0.8, 0.7, 0.5]
BAD_INPUT = [0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.5]


# ---- payload ----------------------------------------------------------------


def test_payload_infers_frames_from_width(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    wave = parse_payload({"image_b64": _wave(5), "hint": "k1"}, artifact.config.input)
    assert wave.frames == 5
    assert wave.hint == "k1"


def test_payload_accepts_eight_cards(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    wave = parse_payload({"image_b64": "data:image/png;base64," + _wave(8)}, artifact.config.input)
    assert wave.frames == 8
    assert wave.hint is None


def test_payload_frames_disagreement_is_unsolvable(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    with pytest.raises(UnsolvableError):
        parse_payload({"image_b64": _wave(5), "frames": 8}, artifact.config.input)


def test_payload_wrong_height_is_unsolvable(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    with pytest.raises(UnsolvableError):
        parse_payload({"image_b64": _wave(5, height=300)}, artifact.config.input)


def test_payload_missing_image_is_unsolvable(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    with pytest.raises(UnsolvableError):
        parse_payload({}, artifact.config.input)


def test_payload_rejects_garbage_base64(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    with pytest.raises(UnsolvableError):
        parse_payload({"image_b64": "%%%not-base64%%%"}, artifact.config.input)


def test_payload_size_cap(artifact_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from captcha.solvers.hopscotch import payload as payload_module

    monkeypatch.setattr(payload_module, "MAX_IMAGE_BYTES", 8)
    artifact = load_artifact(artifact_dir)
    with pytest.raises(UnsolvableError):
        parse_payload({"image_b64": _wave(5)}, artifact.config.input)


def test_foot_to_pixels_adds_card_offset(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    x, y = foot_to_pixels((0.1, 0.5), 2, artifact.config.input, width=1000, height=400)
    assert x == pytest.approx(420.0)
    assert y == pytest.approx(100.0)


def test_foot_to_pixels_clamps(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    x, y = foot_to_pixels((5.0, 5.0), 4, artifact.config.input, width=1000, height=400)
    assert (x, y) == (999.0, 399.0)
