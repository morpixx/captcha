"""RT-DETR ONNX postprocessing: layouts, scaling, shapes, filtering."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from _detection_support import FakeSession

from captcha.domain.errors import ConfigError, ModelInferenceError
from captcha.solvers.detection.detector import DetectorSpec
from captcha.solvers.detection.onnx_detector import OnnxDetector

CLASSES = ("bus", "crosswalk", "traffic light")


def _detector(output: Any, *, spec: DetectorSpec | None = None) -> tuple[OnnxDetector, FakeSession]:
    session = FakeSession(output)
    resolved = spec or DetectorSpec(class_names=CLASSES, input_size=640)
    return OnnxDetector("detector.onnx", resolved, session=session), session


def _image(width: int = 320, height: int = 160) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_xyxy_layout_scales_input_space_to_image() -> None:
    detector, _ = _detector([[320, 160, 640, 480, 0.9, 1]])
    detection = detector.decode([[320, 160, 640, 480, 0.9, 1]], image_hw=(160, 320), conf=0.5)

    assert len(detection) == 1
    assert detection.boxes[0].tolist() == [160.0, 40.0, 320.0, 120.0]
    assert detection.points[0].tolist() == [240.0, 80.0]
    assert detection.scores[0] == pytest.approx(0.9)
    assert detection.classes[0] == 1


def test_xyxy_normalized_boxes() -> None:
    spec = DetectorSpec(class_names=CLASSES, boxes_normalized=True)
    detector, _ = _detector(None, spec=spec)
    detection = detector.decode([[0.25, 0.25, 0.75, 0.75, 0.8, 0]], image_hw=(160, 320), conf=0.1)

    assert detection.boxes[0].tolist() == [80.0, 40.0, 240.0, 120.0]


def test_confidence_and_class_filters() -> None:
    raw = [[0, 0, 20, 20, 0.9, 0], [30, 30, 60, 60, 0.2, 1]]
    detector, _ = _detector(None)

    assert len(detector.decode(raw, image_hw=(160, 320), conf=0.5)) == 1
    assert len(detector.decode(raw, image_hw=(160, 320), conf=0.1)) == 2

    session = FakeSession(raw)
    onnx = OnnxDetector("detector.onnx", DetectorSpec(class_names=CLASSES), session=session)
    filtered = onnx.predict(_image(), conf=0.1, classes=[1])
    assert len(filtered) == 1
    assert int(filtered.classes[0]) == 1


def test_cxcywh_logits_layout() -> None:
    spec = DetectorSpec(class_names=("bus", "car"), input_size=640, output_layout="cxcywh_logits")
    detector, _ = _detector(None, spec=spec)
    raw = [[0.5, 0.5, 0.25, 0.25, 5.0, -5.0]]
    detection = detector.decode(raw, image_hw=(160, 320), conf=0.5)

    assert detection.classes[0] == 0
    assert detection.scores[0] == pytest.approx(0.9933, abs=1e-3)
    assert detection.boxes[0].tolist() == [120.0, 60.0, 200.0, 100.0]


def test_wrong_output_shapes_are_model_errors() -> None:
    detector, _ = _detector(None)
    with pytest.raises(ModelInferenceError, match="6 columns"):
        detector.decode([[0, 0, 10, 10, 0.9]], image_hw=(160, 320), conf=0.1)
    with pytest.raises(ModelInferenceError, match="must be"):
        detector.decode(np.zeros((2, 3, 6)), image_hw=(160, 320), conf=0.1)

    logits_spec = DetectorSpec(class_names=("bus", "car"), output_layout="cxcywh_logits")
    logits_detector, _ = _detector(None, spec=logits_spec)
    with pytest.raises(ModelInferenceError, match="logit count"):
        logits_detector.decode([[0.5, 0.5, 0.2, 0.2, 1.0]], image_hw=(160, 320), conf=0.1)


def test_class_id_outside_vocabulary_is_model_error() -> None:
    detector, _ = _detector(None)
    with pytest.raises(ModelInferenceError, match="outside"):
        detector.decode([[0, 0, 10, 10, 0.9, 7]], image_hw=(160, 320), conf=0.1)


def test_nan_output_is_model_error() -> None:
    detector, _ = _detector(None)
    with pytest.raises(ModelInferenceError, match="NaN"):
        detector.decode([[0, 0, np.nan, 10, 0.9, 0]], image_hw=(160, 320), conf=0.1)


def test_preprocess_is_nchw_and_resized() -> None:
    spec = DetectorSpec(class_names=CLASSES, input_size=64)
    detector, session = _detector([[0, 0, 10, 10, 0.9, 0]], spec=spec)
    detector.predict(_image())

    feed = session.feeds[0]["images"]
    assert feed.shape == (1, 3, 64, 64)
    assert feed.dtype == np.float32


def test_predict_rejects_non_rgb_input() -> None:
    detector, _ = _detector([[0, 0, 10, 10, 0.9, 0]])
    with pytest.raises(ModelInferenceError, match="RGB"):
        detector.predict(np.zeros((10, 10), dtype=np.uint8))


def test_missing_weights_file_is_config_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(InferenceSession=lambda *a, **k: None))
    detector = OnnxDetector(tmp_path / "missing.onnx", DetectorSpec(class_names=CLASSES))
    with pytest.raises(ConfigError, match="weights file missing"):
        detector.predict(_image())


def test_empty_session_output_is_model_error() -> None:
    class EmptySession(FakeSession):
        def run(self, output_names: Any, feeds: dict[str, np.ndarray]) -> list[Any]:
            return []

    detector = OnnxDetector("detector.onnx", DetectorSpec(class_names=CLASSES), session=EmptySession(None))
    with pytest.raises(ModelInferenceError, match="no outputs"):
        detector.predict(_image())


def test_predict_runs_session_and_returns_detection() -> None:
    raw = [[100, 20, 200, 100, 0.75, 2]]
    detector, session = _detector(raw)
    detection = detector.predict(_image(), conf=0.5)

    assert len(detection) == 1
    assert detection.classes.tolist() == [2]
    assert detection.points[0].tolist() == [75.0, 15.0]
    assert session.feeds
