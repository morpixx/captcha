"""Golden manifest for rotate: fake sin/cos weights, ONNX/torch-agnostic wiring.

The manifest under fixtures/golden/rotate-v1.json fixes the answer key for the
rotate solver. No weights ship in the repo: the fake regressor reads the red
channel of the fixture image and returns it as the angle, so the manifest tests
image decoding, path selection and the angle projection, not model quality.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import msgspec
import pytest
from _rotate_support import png_b64
from PIL import Image

from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.errors import ConfigError
from captcha.domain.solution import build_solution
from captcha.services.pipeline.golden import GoldenManifest, run_golden
from captcha.solvers.base import SolverContext
from captcha.solvers.rotate import rotate as rotate_module
from captcha.solvers.rotate.rotate import RotateSolver

FIXTURES = Path(__file__).parent / "fixtures" / "golden"
MANIFEST = FIXTURES / "rotate-v1.json"


def _loader(key: str) -> bytes:
    return (FIXTURES / key).read_bytes()


def _predictor(monkeypatch: pytest.MonkeyPatch, ctx: SolverContext, *, fake: bool):
    async def predict(payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        captcha_type = CaptchaType(str(data.pop("captcha_type")))
        if fake:

            class FakeRegressor:
                @classmethod
                def from_registry(cls, registry: object, **kwargs: object) -> FakeRegressor:
                    return cls()

                def predict(self, rgb: Any) -> tuple[float, float]:
                    return (float(rgb[0, 0, 0]), 1.0)

            monkeypatch.setattr(rotate_module, "AngleRegressor", FakeRegressor)
        else:

            def missing_cv2(*args: Any, **kwargs: Any) -> tuple[float, float]:
                raise ConfigError("opencv required for the classical rotate path", context={})

            monkeypatch.setattr(rotate_module, "estimate_angle", missing_cv2)

        task = Task(
            id=uuid.uuid4(),
            type=captcha_type,
            payload=data,
            preferred_mode=SolveMode.HTTP,
        )
        result = await RotateSolver().solve_http(task, ctx)
        solution = build_solution(captcha_type, msgspec.structs.asdict(result))
        assert solution is not None
        return solution

    return predict


def test_manifest_is_the_rotate_format_contract() -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    assert manifest.artifact == "rotate-v1"
    assert manifest.version == "v1"
    assert manifest.total == 2
    assert manifest.tolerance_px == 0
    assert all(case.type_ == "rotate" for case in manifest.cases)
    assert sorted(int(case.expected["angle"]) for case in manifest.cases) == [30, 200]

    for case in manifest.cases:
        assert case.image is not None
        with Image.open(FIXTURES / case.image) as image:
            assert image.mode == "RGB"
            assert image.size == (64, 64)
            assert image.getpixel((0, 0))[0] == case.expected["angle"]
        assert case.payload["prefer"] == "auto"


def test_manifest_parses_against_the_ml_platform_harness() -> None:
    import json

    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = GoldenManifest.from_mapping(raw, source=str(MANIFEST))

    assert manifest.source == str(MANIFEST)
    assert [case.case_id for case in manifest.cases] == [str(case["id"]) for case in raw["cases"]]


async def test_golden_run_with_fake_regressor_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    report = await run_golden(
        manifest,
        _predictor(monkeypatch, SolverContext(models=object()), fake=True),
        loader=_loader,
    )

    assert report.accuracy == 1.0
    assert report.coverage == 1.0
    assert report.passed is True
    assert report.errors == 0
    assert report.per_type()["rotate"]["total"] == 2


async def test_golden_run_without_weights_is_coverage_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    report = await run_golden(
        manifest,
        _predictor(monkeypatch, SolverContext(models=None), fake=False),
        loader=_loader,
    )

    assert report.answered == 0
    assert report.coverage == 0.0
    assert report.accuracy == 0.0
    assert report.passed is False
    assert report.errors == 2
    assert all("opencv" in (result.error or "") for result in report.results)


def test_fixture_images_are_valid_png() -> None:
    for path in sorted((FIXTURES / "images").glob("*.png")):
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert png_b64().startswith("iVBOR")
