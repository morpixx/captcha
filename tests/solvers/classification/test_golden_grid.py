"""Golden-manifest compatibility with the ml-platform harness.

The manifest under fixtures/golden/grid-v1.json is the format contract for grid
heads: one case per shape (3x3, 4x4, 2x3), expected key 'objects' (the shape
domain/solution.py projects for classification_grid), per-case tolerance 0 so
tile indices are never compared with a pixel epsilon.

Images are synthetic fixture grids, not customer data. No weights, no torch, no
network: a fake head supplies the scores, the real solver + real harness run.
"""

from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path

import msgspec
import pytest
from _support import FakeModels, install_fake_heads, make_spec, solver_context
from PIL import Image

from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.solution import build_solution
from captcha.domain.task import SolverResult
from captcha.services.pipeline.golden import GoldenManifest, run_golden
from captcha.solvers.classification.awswaf import AWSWAFSolver
from captcha.solvers.classification.funcaptcha_grid import FunCaptchaGridSolver
from captcha.solvers.classification.grid import HEADS
from captcha.solvers.classification.hcaptcha import HCaptchaSolver
from captcha.solvers.classification.prompt import normalize_text
from captcha.solvers.classification.recaptcha_grid import RecaptchaGridSolver
from captcha.solvers.classification.shein import SheinSolver

FIXTURES = Path(__file__).parent / "fixtures" / "golden"
MANIFEST = FIXTURES / "grid-v1.json"
TILE_PX = 112

SOLVERS = {
    "hcaptcha-v1": HCaptchaSolver,
    "recaptcha-v1": RecaptchaGridSolver,
    "funcap-v1": FunCaptchaGridSolver,
    "awswaf-v1": AWSWAFSolver,
    "shein-v1": SheinSolver,
}


def _predictor(ctx):
    async def predict(payload: dict[str, object]) -> dict[str, object]:
        solver = SOLVERS[str(payload["head"])]()
        task = Task(
            id=uuid.uuid4(),
            type=CaptchaType.CLASSIFICATION_GRID,
            payload=dict(payload),
            preferred_mode=SolveMode.HTTP,
        )
        result = await solver.solve_http(task, ctx)
        return {"objects": list(result.indices or [])}

    return predict


def _loader(key: str) -> bytes:
    return (FIXTURES / key).read_bytes()


def test_manifest_is_the_grid_format_contract() -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    assert manifest.artifact == "grid-heads"
    assert manifest.version == "v1"
    assert manifest.total == 6
    assert manifest.tolerance_px == 0
    assert all(case.tolerance_px == 0 for case in manifest.cases), "indices are exact, never pixel-tolerant"
    assert all(case.type_ == "classification_grid" for case in manifest.cases)

    heads = {str(case.payload["head"]) for case in manifest.cases}
    assert heads == set(HEADS), "one golden case per enrolled head"

    shapes = set()
    for case in manifest.cases:
        head = str(case.payload["head"])
        rows = int(case.payload["rows"])
        cols = int(case.payload["cols"])
        contract = HEADS[head]
        contract.resolve(rows, cols)
        shapes.add((rows, cols))

        assert case.image is not None
        with Image.open(FIXTURES / case.image) as image:
            assert image.size == (cols * TILE_PX, rows * TILE_PX)
            assert image.mode == "RGB"

        expected = case.expected["objects"]
        assert expected, "a golden case must name at least one tile"
        assert all(isinstance(index, int) and 0 <= index < rows * cols for index in expected)
        assert str(case.payload["prompt"]) in _prompts_for(manifest, head)

    assert shapes == {(3, 3), (4, 4), (2, 3)}
    assert {str(case.payload["prompt"]) for case in manifest.cases} == {
        "select all images with a traffic light",
        "crosswalk",
        "bus",
        "animal",
        "wrench",
        "sneakers",
    }


def _prompts_for(manifest: GoldenManifest, head: str) -> set[str]:
    return {str(case.payload["prompt"]) for case in manifest.cases if case.payload.get("head") == head}


def test_manifest_parses_against_the_ml_platform_harness() -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest = GoldenManifest.from_mapping(raw, source=str(MANIFEST))

    assert manifest.source == str(MANIFEST)
    assert [case.case_id for case in manifest.cases] == [str(case["id"]) for case in raw["cases"]]
    assert manifest.cases[0].tags == ("hcaptcha-v1", "3x3")


async def test_golden_run_with_fake_heads_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = GoldenManifest.from_file(MANIFEST)
    heads: dict[str, tuple[object, dict[str, list[int]]]] = {}
    for head in HEADS:
        cases = [case for case in manifest.cases if case.payload["head"] == head]
        prompts = [str(case.payload["prompt"]) for case in cases]
        labels = [normalize_text(prompt) for prompt in prompts]
        picked = {str(case.payload["prompt"]): list(case.expected["objects"]) for case in cases}
        heads[head] = (make_spec(HEADS[head], labels), picked)
    install_fake_heads(monkeypatch, heads)

    report = await run_golden(
        manifest,
        _predictor(solver_context(FakeModels())),
        loader=_loader,
    )

    assert report.accuracy == 1.0
    assert report.coverage == 1.0
    assert report.passed is True
    assert report.errors == 0
    assert report.per_type()["classification_grid"]["total"] == 6


async def test_golden_run_without_weights_is_coverage_failure() -> None:
    manifest = GoldenManifest.from_file(MANIFEST)

    report = await run_golden(
        manifest,
        _predictor(solver_context(None)),
        loader=_loader,
    )

    assert report.answered == 0
    assert report.coverage == 0.0
    assert report.accuracy == 0.0
    assert report.passed is False
    assert report.errors == 6
    assert all("requires ctx.models" in (result.error or "") for result in report.results)


def test_grid_result_projects_to_objects_key() -> None:
    result = SolverResult(indices=[1, 3], solve_mode=SolveMode.HTTP)
    solution = build_solution(CaptchaType.CLASSIFICATION_GRID, msgspec.structs.asdict(result))
    assert solution == {"objects": [1, 3]}


def test_fixture_images_are_valid_png() -> None:
    for path in sorted((FIXTURES / "images").glob("*.png")):
        raw = path.read_bytes()
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(base64.b64encode(raw)) > 0
