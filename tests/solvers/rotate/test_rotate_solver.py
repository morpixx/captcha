"""Rotate solver: ML/classical selection, fallbacks, stable errors."""

from __future__ import annotations

import msgspec
import pytest
from _rotate_support import png_b64, task

from captcha.domain import CaptchaType
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.solvers.base import SolverContext
from captcha.solvers.rotate import rotate as rotate_module
from captcha.solvers.rotate.rotate import RotateSolver


class FakeRegressor:
    def __init__(self, angle: float = 123.0, confidence: float = 0.8, error: Exception | None = None) -> None:
        self.angle = angle
        self.confidence = confidence
        self.error = error
        self.calls = 0

    def predict(self, rgb: object) -> tuple[float, float]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return (self.angle, self.confidence)


def _install_regressor(monkeypatch: pytest.MonkeyPatch, regressor: FakeRegressor) -> None:
    class Loader:
        @classmethod
        def from_registry(cls, registry: object, **kwargs: object) -> FakeRegressor:
            return regressor

    monkeypatch.setattr(rotate_module, "AngleRegressor", Loader)


def _install_classical(monkeypatch: pytest.MonkeyPatch, angle: float = 30.0, confidence: float = 0.6) -> None:
    monkeypatch.setattr(rotate_module, "estimate_angle", lambda rgb, **kwargs: (angle, confidence))


async def test_classical_preference_skips_the_regressor(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_classical(monkeypatch)

    result = await RotateSolver().solve_http(
        task(image=png_b64(), prefer="classical"),
        SolverContext(models=None),
    )

    assert result.angle == 30.0
    assert result.extra is not None
    assert result.extra["path"] == "classical"
    solution = build_solution(CaptchaType.ROTATE, msgspec.structs.asdict(result))
    assert solution is not None
    assert solution["angle"] == 30.0


async def test_auto_without_registry_falls_back_to_classical(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_classical(monkeypatch, angle=310.0)

    result = await RotateSolver().solve_http(task(image=png_b64()), SolverContext(models=None))

    assert result.angle == 310.0
    assert result.extra is not None
    assert result.extra["path"] == "classical_fallback"
    assert "requires ctx.models" in result.extra["fallback_reason"]


async def test_prefer_ml_without_registry_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"requires ctx\.models"):
        await RotateSolver().solve_http(
            task(image=png_b64(), prefer="ml"),
            SolverContext(models=None),
        )


async def test_auto_uses_regressor_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    regressor = FakeRegressor(angle=123.0)
    _install_regressor(monkeypatch, regressor)

    result = await RotateSolver().solve_http(task(image=png_b64()), SolverContext(models=object()))

    assert result.angle == 123.0
    assert result.extra is not None
    assert result.extra["path"] == "ml"
    assert result.extra["model"] == "rotate-v1"
    assert result.extra["confidence"] == pytest.approx(0.8)
    assert regressor.calls == 1


async def test_inference_failure_falls_back_to_classical(monkeypatch: pytest.MonkeyPatch) -> None:
    regressor = FakeRegressor(error=ModelInferenceError("bad tensor"))
    _install_regressor(monkeypatch, regressor)
    _install_classical(monkeypatch, angle=45.0, confidence=0.5)

    result = await RotateSolver().solve_http(task(image=png_b64()), SolverContext(models=object()))

    assert result.angle == 45.0
    assert result.extra is not None
    assert result.extra["path"] == "classical_fallback"
    assert result.extra["fallback_reason"] == "bad tensor"


async def test_prefer_ml_propagates_inference_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_regressor(monkeypatch, FakeRegressor(error=ModelInferenceError("bad tensor")))

    with pytest.raises(ModelInferenceError, match="bad tensor"):
        await RotateSolver().solve_http(
            task(image=png_b64(), prefer="ml"),
            SolverContext(models=object()),
        )


async def test_invalid_preference_and_payload_are_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="prefer"):
        await RotateSolver().solve_http(task(image=png_b64(), prefer="magic"), SolverContext())
    with pytest.raises(UnsolvableError, match="missing 'image'"):
        await RotateSolver().solve_http(task(prefer="classical"), SolverContext())
    with pytest.raises(UnsolvableError, match="decode"):
        await RotateSolver().solve_http(task(image="aGVsbG8=", prefer="classical"), SolverContext())


async def test_prepare_never_fails_without_models() -> None:
    solver = RotateSolver()
    await solver.prepare(SolverContext(models=None))
    assert solver._regressor is None


def test_rotate_solver_metadata() -> None:
    assert RotateSolver.info.types == (CaptchaType.ROTATE,)
    assert RotateSolver.info.http is True
