"""Solver behaviour with a fake model: no torch, no GPU, no training checkout.

The point is the wire contract: payload in, 1-based index + pixel coordinates out,
and a hard refusal (not a guess) when the artifact or the calibration says no.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import msgspec
import pytest
from conftest import release_payload, write_artifact
from PIL import Image

from captcha.domain import CaptchaType, SolveMode, SolverResult, Task
from captcha.domain.errors import ConfigError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.solvers import SolverContext
from captcha.solvers.hopscotch.artifact import load_artifact
from captcha.solvers.hopscotch.calibration import Observation
from captcha.solvers.hopscotch.solver import HopscotchSolver
from captcha.solvers.loader import SOLVERS
from captcha.solvers.registry import SolverRegistry


class FakeBackend:
    """Stands in for the network: returns whatever observation it was given."""

    def __init__(self, observation: Observation) -> None:
        self.observation = observation
        self.calls = 0

    def predict(self, wave: Any) -> Observation:
        self.calls += 1
        assert wave.frames in (5, 8)
        return self.observation


def _task(payload: dict[str, Any]) -> Task:
    return Task(id=__import__("uuid").uuid4(), type=CaptchaType.HOPSCOTCH, payload=payload)


def _image_b64(frames: int = 5) -> str:
    image = Image.new("RGB", (frames * 200, 400), (12, 34, 56))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _ctx(artifact_dir: Path, backend: FakeBackend) -> SolverContext:
    return SolverContext(
        extra={
            "hopscotch_artifact_dir": str(artifact_dir),
            "hopscotch_backend": backend,
        }
    )


GOOD_FEATURES = (0.6, 0.4, 0.5, 0.9, 0.5, 0.8, 0.7, 0.5)


@pytest.fixture
def solver() -> HopscotchSolver:
    return HopscotchSolver()


async def test_solve_returns_one_based_index_and_pixels(
    solver: HopscotchSolver, artifact_dir: Path
) -> None:
    backend = FakeBackend(Observation(3, (0.25, 0.5), GOOD_FEATURES, True, "valid"))
    await solver.prepare(_ctx(artifact_dir, backend))
    result = await solver.solve_http(_task({"image_b64": _image_b64(5)}), _ctx(artifact_dir, backend))

    assert isinstance(result, SolverResult)
    assert result.extra["index"] == 3 + 1  # 0-based model answer -> 1-based card
    assert result.extra["card_index"] == 3
    assert result.indices == [3]
    x, y = result.extra["coordinates"]
    assert (x, y) == pytest.approx((650.0, 100.0))
    assert result.extra["frames"] == 5
    assert backend.calls == 1


async def test_solver_result_projects_into_the_solution_shape(
    solver: HopscotchSolver, artifact_dir: Path
) -> None:
    backend = FakeBackend(Observation(1, (0.5, 0.5), GOOD_FEATURES, True, "valid"))
    result = await solver.solve_http(
        _task({"image_b64": _image_b64(8)}), _ctx(artifact_dir, backend)
    )
    solution = build_solution(CaptchaType.HOPSCOTCH, msgspec.structs.asdict(result))
    assert solution["index"] == 2
    assert solution["coordinates"] == [300.0, 100.0]


async def test_abstention_raises_instead_of_answering(
    solver: HopscotchSolver, artifact_dir: Path
) -> None:
    backend = FakeBackend(Observation(0, (0.5, 0.5), (0.0,) * 8, True, "valid"))
    with pytest.raises(UnsolvableError) as caught:
        await solver.solve_http(_task({"image_b64": _image_b64(5)}), _ctx(artifact_dir, backend))
    context = getattr(caught.value, "context", {}) or {}
    assert context.get("reason") == "low_correctness_score"


async def test_not_ready_release_abstains(solver: HopscotchSolver, tmp_path: Path) -> None:
    directory = write_artifact(
        tmp_path / "beta", release=release_payload(status="not_ready", threshold=None)
    )
    backend = FakeBackend(Observation(2, (0.5, 0.5), GOOD_FEATURES, True, "valid"))
    with pytest.raises(UnsolvableError) as caught:
        await solver.solve_http(_task({"image_b64": _image_b64(5)}), _ctx(directory, backend))
    context = getattr(caught.value, "context", {}) or {}
    assert context.get("reason") == "uncalibrated"


async def test_bad_payload_never_reaches_the_model(
    solver: HopscotchSolver, artifact_dir: Path
) -> None:
    backend = FakeBackend(Observation(0, None, GOOD_FEATURES, True, "valid"))
    with pytest.raises(UnsolvableError):
        await solver.solve_http(_task({"frames": 5}), _ctx(artifact_dir, backend))
    assert backend.calls == 0


async def test_prepare_requires_a_configured_artifact(
    solver: HopscotchSolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOPSCOTCH_ARTIFACT_DIR", raising=False)
    with pytest.raises(ConfigError):
        await solver.prepare(SolverContext())


async def test_prepare_rejects_a_broken_artifact(
    solver: HopscotchSolver, artifact_dir: Path
) -> None:
    (artifact_dir / "model.safetensors").unlink()
    with pytest.raises(ConfigError):
        await solver.prepare(_ctx(artifact_dir, FakeBackend(Observation(0, None, None, False, "x"))))


def test_solver_is_enrolled_and_pickable() -> None:
    assert "captcha.solvers.hopscotch.solver:HopscotchSolver" in SOLVERS
    registry = SolverRegistry()
    registry.register(HopscotchSolver)
    picked = registry.pick(CaptchaType.HOPSCOTCH, mode=SolveMode.HTTP, allow_experimental=True)
    assert picked is HopscotchSolver
    assert registry.pick(CaptchaType.HOPSCOTCH, mode=SolveMode.BROWSER) is None


def test_artifact_exposes_the_serving_geometry(artifact_dir: Path) -> None:
    artifact = load_artifact(artifact_dir)
    assert artifact.config.input.width_for == {5: 1000, 8: 1600}
    assert artifact.config.checkpoint_epoch == 39
    assert artifact.release.split_sha256 == "a" * 64
