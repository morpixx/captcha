"""Golden manifest for detection: one case per solver, fake ONNX detections.

The manifest under fixtures/golden/detection-v1.json is the format contract for
detection artifacts: canonical projected keys per type, per-axis pixel
tolerance, and payloads that carry the synthetic detector rows (_boxes, _scores,
_classes) because no weights ship in the repo.
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

import msgspec
import pytest
from _detection_support import FakeDetector, png_b64, solver_context
from PIL import Image

from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.solution import build_solution
from captcha.services.pipeline.golden import GoldenManifest, run_golden
from captcha.solvers.detection import detector as detector_api
from captcha.solvers.detection.bbox import BBoxSolver
from captcha.solvers.detection.click import ClickSolver
from captcha.solvers.detection.drag import DragDropSolver
from captcha.solvers.detection.draw import DrawAroundSolver

FIXTURES = Path(__file__).parent / "fixtures" / "golden"
MANIFEST = FIXTURES / "detection-v1.json"

SOLVERS = {
    CaptchaType.CLICK: ClickSolver,
    CaptchaType.BOUNDING_BOX: BBoxSolver,
    CaptchaType.DRAW_AROUND: DrawAroundSolver,
    CaptchaType.DRAG_DROP: DragDropSolver,
}
CLASS_NAMES = ("bus", "crosswalk", "traffic light")


def _loader(key: str) -> bytes:
    return (FIXTURES / key).read_bytes()


def _predictor(ctx: Any, monkeypatch: pytest.MonkeyPatch, *, fake: bool):
    async def predict(payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        captcha_type = CaptchaType(str(data.pop("captcha_type")))
        fake_rows = {
            "boxes": data.pop("_boxes"),
            "scores": data.pop("_scores"),
            "classes": data.pop("_classes"),
        }
        if fake:
            detector = FakeDetector(class_names=CLASS_NAMES, **fake_rows)
            monkeypatch.setattr(detector_api, "detector_for_task", lambda *args, **kwargs: detector)
        solver = SOLVERS[captcha_type]()
        task = Task(
            id=uuid.uuid4(),
            type=captcha_type,
            payload=data,
            preferred_mode=SolveMode.HTTP,
        )
        result = await solver.solve_http(task, ctx)
        solution = build_solution(captcha_type, msgspec.structs.asdict(result))
        assert solution is not None
        return solution

    return predict


def test_manifest_is_the_detection_format_contract() -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    assert manifest.artifact == "detection-v1"
    assert manifest.version == "v1"
    assert manifest.total == 4
    assert manifest.tolerance_px == 1
    assert manifest.min_accuracy == 1.0
    assert manifest.min_coverage == 1.0

    types = {case.type_ for case in manifest.cases}
    assert types == {"click", "bounding_box", "draw_around", "drag_drop"}

    for case in manifest.cases:
        captcha_type = CaptchaType(str(case.payload["captcha_type"]))
        assert captcha_type.value == case.type_
        assert case.image is not None
        with Image.open(FIXTURES / case.image) as image:
            assert image.size == (320, 240)
            assert image.mode == "RGB"

        boxes = case.payload["_boxes"]
        scores = case.payload["_scores"]
        classes = case.payload["_classes"]
        assert len(boxes) == len(scores) == len(classes) >= 1
        assert all(len(box) == 4 for box in boxes)
        assert all(0 <= int(class_id) < len(CLASS_NAMES) for class_id in classes)
        expected_key = "bbox" if case.type_ == "bounding_box" else "coordinates"
        assert set(case.expected) == {expected_key}


def test_manifest_parses_against_the_ml_platform_harness() -> None:
    import json

    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = GoldenManifest.from_mapping(raw, source=str(MANIFEST))

    assert manifest.source == str(MANIFEST)
    assert [case.case_id for case in manifest.cases] == [str(case["id"]) for case in raw["cases"]]
    assert manifest.cases[0].tags == ("click", "onnx")


async def test_golden_run_with_fake_detector_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    report = await run_golden(
        manifest,
        _predictor(solver_context(object()), monkeypatch, fake=True),
        loader=_loader,
    )

    assert report.accuracy == 1.0
    assert report.coverage == 1.0
    assert report.passed is True
    assert report.errors == 0
    assert report.per_type()["click"]["total"] == 1


async def test_golden_run_without_weights_is_coverage_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    report = await run_golden(
        manifest,
        _predictor(solver_context(None), monkeypatch, fake=False),
        loader=_loader,
    )

    assert report.answered == 0
    assert report.coverage == 0.0
    assert report.accuracy == 0.0
    assert report.passed is False
    assert report.errors == 4
    assert all("requires ctx.models" in (result.error or "") for result in report.results)


def test_fixture_images_are_valid_png() -> None:
    for path in sorted((FIXTURES / "images").glob("*.png")):
        raw = path.read_bytes()
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(base64.b64encode(raw)) > 0
        assert png_b64().startswith("iVBOR")
