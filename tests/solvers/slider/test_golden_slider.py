"""Golden manifest for slider: classical matching, no weights at all.

The manifest under fixtures/golden/slider-v1.json fixes the answer key for the
edge/template matcher: one synthetic background per gap position, one shared
piece, exact offset tolerance of 1 px. Images are generated fixtures, not
customer data.
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any

import msgspec
from _slider_support import slider_context
from PIL import Image

from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.solution import build_solution
from captcha.services.pipeline.golden import GoldenManifest, run_golden
from captcha.solvers.slider.slider import SliderSolver

FIXTURES = Path(__file__).parent / "fixtures" / "golden"
MANIFEST = FIXTURES / "slider-v1.json"


def _loader(key: str) -> bytes:
    return (FIXTURES / key).read_bytes()


def _predictor():
    async def predict(payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        piece_key = str(data.pop("piece_image"))
        data["bg"] = data.pop("image_b64")
        data["piece"] = base64.b64encode(_loader(piece_key)).decode("ascii")
        task = Task(
            id=uuid.uuid4(),
            type=CaptchaType.SLIDER,
            payload=data,
            preferred_mode=SolveMode.HTTP,
        )
        result = await SliderSolver().solve_http(task, slider_context())
        solution = build_solution(CaptchaType.SLIDER, msgspec.structs.asdict(result))
        assert solution is not None
        return solution

    return predict


def test_manifest_is_the_slider_format_contract() -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    assert manifest.artifact == "slider-v1"
    assert manifest.version == "v1"
    assert manifest.total == 3
    assert manifest.tolerance_px == 1
    assert all(case.type_ == "slider" for case in manifest.cases)

    expected_offsets = sorted(int(case.expected["offset"]) for case in manifest.cases)
    assert expected_offsets == [80, 100, 160]

    for case in manifest.cases:
        assert case.image is not None
        with Image.open(FIXTURES / case.image) as image:
            assert image.mode == "RGB"
            assert image.width > 40 and image.height > 40
        piece_path = FIXTURES / str(case.payload["piece_image"])
        with Image.open(piece_path) as piece:
            assert piece.mode == "RGBA"
            assert piece.size == (50, 50)
        assert case.payload["backend"] == "numpy"


def test_manifest_parses_against_the_ml_platform_harness() -> None:
    import json

    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = GoldenManifest.from_mapping(raw, source=str(MANIFEST))

    assert manifest.source == str(MANIFEST)
    assert [case.case_id for case in manifest.cases] == [str(case["id"]) for case in raw["cases"]]


async def test_golden_run_with_classical_matcher_passes() -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    report = await run_golden(manifest, _predictor(), loader=_loader)

    assert report.accuracy == 1.0
    assert report.coverage == 1.0
    assert report.passed is True
    assert report.errors == 0
    assert report.per_type()["slider"]["total"] == 3


async def test_golden_run_with_broken_piece_is_coverage_failure() -> None:
    manifest = GoldenManifest.from_mapping(
        {
            "artifact": "slider-v1",
            "version": "broken",
            "cases": [
                {
                    "id": "broken-0001",
                    "type": "slider",
                    "image": "images/slider-bg-0001.png",
                    "payload": {"piece_image": "pieces/missing.png"},
                    "expected": {"offset": 100},
                }
            ],
        }
    )

    report = await run_golden(manifest, _predictor(), loader=_loader)

    assert report.answered == 0
    assert report.errors == 1
    assert report.coverage == 0.0
    assert report.passed is False


def test_fixture_images_are_valid_png() -> None:
    for path in sorted((FIXTURES / "images").glob("*.png")):
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    for path in sorted((FIXTURES / "pieces").glob("*.png")):
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
