"""Unified detector interface: result shape, vocabulary, spec, loader."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from _detection_support import FakeModels, make_meta

from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.solvers.detection.detector import (
    Detection,
    coerce_confidence,
    resolve_class_ids,
)
from captcha.solvers.detection.detector_loader import clear_detector_cache, load_detector
from captcha.solvers.detection.detector_spec import DetectorSpec, spec_from_meta
from captcha.solvers.detection.onnx_detector import OnnxDetector


def test_from_boxes_derives_points_and_clamps() -> None:
    detection = Detection.from_boxes(
        [[-10, -5, 50, 60], [400, 300, 500, 400], [10, 10, 10, 20]],
        [0.9, 0.8, 0.7],
        [0, 1, 2],
        image_hw=(100, 200),
    )

    assert len(detection) == 1
    assert detection.boxes[0].tolist() == [0.0, 0.0, 50.0, 60.0]
    assert detection.points[0].tolist() == [25.0, 30.0]


def test_empty_detection_is_valid() -> None:
    detection = Detection.from_boxes([], [], [])
    assert len(detection) == 0
    assert detection.best() is None
    assert detection.filtered(conf=0.5) is not None


def test_mismatched_arrays_are_model_errors() -> None:
    with pytest.raises(ModelInferenceError, match="mismatched"):
        Detection.from_boxes([[0, 0, 10, 10]], [0.5], [])

    with pytest.raises(ModelInferenceError, match="points"):
        Detection(np.zeros((1, 4)), np.ones(1), np.zeros(1, dtype=np.int32), points=np.zeros((2, 2)))

    with pytest.raises(ModelInferenceError, match="x2"):
        Detection(np.asarray([[10, 10, 5, 20]]), np.ones(1), np.zeros(1, dtype=np.int32))

    with pytest.raises(ModelInferenceError, match="NaN"):
        Detection(np.asarray([[np.nan, 0, 10, 10]]), np.ones(1), np.zeros(1, dtype=np.int32))


def test_filter_and_best() -> None:
    detection = Detection.from_boxes(
        [[0, 0, 10, 10], [20, 20, 40, 40], [50, 50, 60, 60]],
        [0.2, 0.9, 0.7],
        [0, 1, 1],
    )
    assert detection.best() == 1
    assert detection.best(class_ids=[1]) == 1
    assert len(detection.filtered(conf=0.5)) == 2
    assert len(detection.filtered(conf=0.5, class_ids=[0])) == 0


def test_resolve_class_ids_exact_substring_and_ids() -> None:
    names = ("bus", "crosswalk", "traffic light", "traffic sign")
    assert resolve_class_ids(names, "bus") == [0]
    assert resolve_class_ids(names, "traffic") == [3]  # shortest matching name wins
    assert resolve_class_ids(names, 1) == [1]
    assert resolve_class_ids(names, ["bus", "crosswalk", "bus"]) == [0, 1]


def test_resolve_class_ids_errors() -> None:
    with pytest.raises(UnsolvableError, match="not found"):
        resolve_class_ids(("bus",), "dragon")
    with pytest.raises(UnsolvableError, match="outside"):
        resolve_class_ids(("bus",), 3)
    with pytest.raises(ConfigError, match="vocabulary"):
        resolve_class_ids((), "bus")
    with pytest.raises(UnsolvableError, match="target"):
        resolve_class_ids(("bus",), None)


def test_spec_from_meta_parses_published_contract() -> None:
    meta = make_meta(
        schema={
            "class_names": ["bus", "car"],
            "input_size": 512,
            "output_layout": "cxcywh_logits",
            "score_threshold": 0.4,
            "boxes_normalized": True,
        }
    )
    spec = spec_from_meta(meta)

    assert spec.class_names == ("bus", "car")
    assert spec.input_size == 512
    assert spec.output_layout == "cxcywh_logits"
    assert spec.score_threshold == 0.4
    assert spec.boxes_normalized is True


def test_spec_from_meta_rejects_bad_layout_and_labels() -> None:
    with pytest.raises(ConfigError, match="output_layout"):
        spec_from_meta(make_meta(schema={"output_layout": "nms"}))
    with pytest.raises(ConfigError, match="duplicates"):
        spec_from_meta(make_meta(schema={"class_names": ["bus", "bus"]}))
    with pytest.raises(ConfigError, match="class map"):
        spec_from_meta(make_meta(schema={"labels": {"bus": "x"}}))
    with pytest.raises(ConfigError, match="input_size"):
        spec_from_meta(make_meta(schema={"input_size": 0}))


def test_spec_falls_back_to_indexed_meta_labels() -> None:
    meta = make_meta(schema={}, labels={"bus": "0", "car": "1"})
    spec = spec_from_meta(meta)
    assert spec.class_names == ("bus", "car")


def test_confidence_coercion_is_a_domain_error() -> None:
    assert coerce_confidence(None) == 0.25
    assert coerce_confidence("0.4") == 0.4
    for bad in ("abc", 0, -1, 1.5, float("nan")):
        with pytest.raises(UnsolvableError, match="conf"):
            coerce_confidence(bad)


def test_load_detector_requires_registry() -> None:
    with pytest.raises(ConfigError, match=r"requires ctx\.models"):
        load_detector(None, "click-v1")


def test_load_detector_wraps_broken_meta() -> None:
    models = FakeModels(meta_error=ValueError("bad meta"))
    with pytest.raises(ConfigError, match="unusable detector meta"):
        load_detector(models, "click-v1")


def test_non_onnx_production_artifact_is_config_error() -> None:
    models = FakeModels(meta=make_meta(framework="torch"))
    with pytest.raises(ConfigError, match="must be ONNX"):
        load_detector(models, "click-v1")


def test_ultralytics_requires_explicit_dev_opt_in() -> None:
    models = FakeModels(meta=make_meta(), weights_path="missing.pt")
    with pytest.raises(ConfigError, match="dev-only"):
        load_detector(models, "click-v1", kind="ultralytics")

    # Even with the flag, a missing file/runtime stays ERR_CONFIG, never an import leak.
    with pytest.raises(ConfigError):
        load_detector(models, "click-v1", kind="ultralytics", allow_dev=True)


def test_unknown_kind_is_config_error() -> None:
    with pytest.raises(ConfigError, match="unknown detector kind"):
        load_detector(FakeModels(meta=make_meta()), "click-v1", kind="tensorrt")


def test_onnx_detector_missing_runtime_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from captcha.solvers.detection.onnx_detector import OnnxDetector

    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    detector = OnnxDetector("missing.onnx", DetectorSpec(class_names=("bus",)))
    with pytest.raises(ConfigError, match="onnxruntime"):
        detector.predict(np.zeros((16, 16, 3), dtype=np.uint8))


def test_onnx_detector_session_init_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from captcha.solvers.detection.onnx_detector import OnnxDetector

    weights = tmp_path / "detector.onnx"
    weights.write_bytes(b"not-a-real-onnx")

    class Boom:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("bad model")

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=Boom))
    detector = OnnxDetector(weights, DetectorSpec(class_names=("bus",)))
    with pytest.raises(ModelInferenceError, match="session init"):
        detector.predict(np.zeros((16, 16, 3), dtype=np.uint8))


def test_load_detector_builds_and_caches_onnx(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from _detection_support import FakeSession

    weights = tmp_path / "click.onnx"
    weights.write_bytes(b"fake-onnx")
    models = FakeModels(
        meta=make_meta(schema={"class_names": ["bus"], "input_size": 32}),
        weights_path=weights,
    )
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(InferenceSession=lambda *args, **kwargs: FakeSession([[0, 0, 16, 16, 0.9, 0]])),
    )

    first = load_detector(models, "click-v1")
    second = load_detector(models, "click-v1")

    assert isinstance(first, OnnxDetector)
    assert first is second
    assert first.class_names == ("bus",)

    clear_detector_cache()
    assert load_detector(models, "click-v1") is not first
