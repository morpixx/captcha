"""Solver-path tests: fake head/backbone, real payload slicing and errors.

No torch, no network, no model bucket. The point is the contract between the
solver, the head spec, and the error taxonomy: indices on success, ERR_CONFIG
or ERR_MODEL_INFERENCE when weights are absent/broken, never a guessed answer.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from _support import FakeModels, make_meta, make_spec, patch_backbone

from captcha.domain import CaptchaType, SolveMode, Task
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.solvers.classification.base_grid import GridClassifier
from captcha.solvers.classification.funcaptcha_grid import FunCaptchaGridSolver
from captcha.solvers.classification.grid import HEADS
from captcha.solvers.classification.hcaptcha import HCaptchaSolver
from captcha.solvers.classification.recaptcha_grid import RecaptchaGridSolver


def _task(payload: dict[str, object]) -> Task:
    return Task(
        id=uuid4(),
        type=CaptchaType.CLASSIFICATION_GRID,
        payload=payload,
        preferred_mode=SolveMode.HTTP,
    )


def _payload(image_b64: str, prompt: str, **extra: object) -> dict[str, object]:
    return {"image_b64": image_b64, "prompt": prompt, **extra}


class _Logger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.calls.append((event, kwargs))


async def test_hcaptcha_3x3_returns_indices(fake_stack, grid_png_b64, context) -> None:
    spec = make_spec(HEADS["hcaptcha-v1"], ["bus", "traffic light"])
    fake_stack(spec, {"select all images with a bus": [0, 4]})
    solver = HCaptchaSolver()

    result = await solver.solve_http(
        _task(_payload(grid_png_b64(3, 3), "select all images with a bus")),
        context(FakeModels()),
    )

    assert result.indices == [0, 4]
    assert result.extra is not None
    assert result.extra["head"] == "hcaptcha-v1"
    assert (result.extra["rows"], result.extra["cols"]) == (3, 3)
    assert result.extra["threshold"] == pytest.approx(0.5)


async def test_funcap_defaults_to_2x3(fake_stack, grid_png_b64, context) -> None:
    spec = make_spec(HEADS["funcap-v1"], ["animal"])
    fake_stack(spec, {"animal": [1, 5]})
    solver = FunCaptchaGridSolver()

    result = await solver.solve_http(
        _task(_payload(grid_png_b64(2, 3), "animal", head="funcap-v1")),
        context(FakeModels()),
    )

    assert result.indices == [1, 5]
    assert (result.extra["rows"], result.extra["cols"]) == (2, 3)


async def test_recaptcha_4x4_payload_shape(fake_stack, grid_png_b64, context) -> None:
    spec = make_spec(HEADS["recaptcha-v1"], ["bus"])
    fake_stack(spec, {"bus": [0, 15]})
    solver = RecaptchaGridSolver()

    result = await solver.solve_http(
        _task(_payload(grid_png_b64(4, 4), "bus", rows=4, cols=4)),
        context(FakeModels()),
    )

    assert result.indices == [0, 15]
    assert (result.extra["rows"], result.extra["cols"]) == (4, 4)


async def test_body_alias_is_the_legacy_grid_image_field(fake_stack, grid_png_b64, context) -> None:
    spec = make_spec(HEADS["hcaptcha-v1"], ["bus"])
    fake_stack(spec, {"bus": [2]})
    solver = HCaptchaSolver()

    result = await solver.solve_http(
        _task({"body": grid_png_b64(), "prompt": "bus"}),
        context(FakeModels()),
    )

    assert result.indices == [2]


async def test_payload_threshold_can_empty_the_answer(fake_stack, grid_png_b64, context) -> None:
    spec = make_spec(HEADS["hcaptcha-v1"], ["bus"])
    fake_stack(spec, {"bus": [4]})
    solver = HCaptchaSolver()

    result = await solver.solve_http(
        _task(_payload(grid_png_b64(), "bus", threshold=0.99)),
        context(FakeModels()),
    )

    assert result.indices == [], "no argmax fallback: below threshold means no click"


async def test_unknown_prompt_is_inference_error(fake_stack, grid_png_b64, context) -> None:
    spec = make_spec(HEADS["hcaptcha-v1"], ["bus"])
    fake_stack(spec, {})
    solver = HCaptchaSolver()

    with pytest.raises(ModelInferenceError, match="no label matching"):
        await solver.solve_http(
            _task(_payload(grid_png_b64(), "motorcycle")),
            context(FakeModels()),
        )


async def test_head_cannot_be_asked_for_unlisted_shape(fake_stack, grid_png_b64, context) -> None:
    spec = make_spec(HEADS["hcaptcha-v1"], ["bus"])
    fake_stack(spec, {"bus": [0]})
    solver = HCaptchaSolver()

    with pytest.raises(UnsolvableError, match="does not serve"):
        await solver.solve_http(
            _task(_payload(grid_png_b64(2, 3), "bus", rows=2, cols=3)),
            context(FakeModels()),
        )


async def test_unknown_head_is_config_error(grid_png_b64, context) -> None:
    solver = HCaptchaSolver()
    with pytest.raises(ConfigError, match="unknown classification head"):
        await solver.solve_http(
            _task(_payload(grid_png_b64(), "bus", head="grid-v9")),
            context(FakeModels()),
        )


async def test_missing_registry_is_config_error(grid_png_b64, context) -> None:
    solver = HCaptchaSolver()
    with pytest.raises(ConfigError) as exc:
        await solver.solve_http(
            _task(_payload(grid_png_b64(), "bus")),
            context(None),
        )
    assert exc.value.code == "ERR_CONFIG"


async def test_missing_meta_is_config_error(grid_png_b64, context, monkeypatch) -> None:
    patch_backbone(monkeypatch)
    solver = HCaptchaSolver()
    models = FakeModels(meta_error=ConfigError("meta.json missing", context={"name": "hcaptcha-v1"}))

    with pytest.raises(ConfigError) as exc:
        await solver.solve_http(_task(_payload(grid_png_b64(), "bus")), context(models))
    assert exc.value.code == "ERR_CONFIG"


async def test_missing_labels_is_config_error(grid_png_b64, context, monkeypatch) -> None:
    patch_backbone(monkeypatch)
    import msgspec

    contract = HEADS["hcaptcha-v1"]
    meta = msgspec.structs.replace(make_meta(contract, ["bus"]), labels={}, input_schema={})
    solver = HCaptchaSolver()

    with pytest.raises(ConfigError) as exc:
        await solver.solve_http(_task(_payload(grid_png_b64(), "bus")), context(FakeModels(meta=meta)))
    assert exc.value.code == "ERR_CONFIG"


async def test_corrupt_weights_is_model_inference_error(grid_png_b64, context, monkeypatch) -> None:
    patch_backbone(monkeypatch)
    contract = HEADS["hcaptcha-v1"]
    solver = HCaptchaSolver()
    models = FakeModels(
        meta=make_meta(contract, ["bus"]),
        download_error=ModelInferenceError("sha256 mismatch", context={"name": contract.name}),
    )

    with pytest.raises(ModelInferenceError) as exc:
        await solver.solve_http(_task(_payload(grid_png_b64(), "bus")), context(models))
    assert exc.value.code == "ERR_MODEL_INFERENCE"


async def test_prepare_boots_without_registry(context, monkeypatch) -> None:
    patch_backbone(monkeypatch)
    await HCaptchaSolver().prepare(context(None))


async def test_prepare_warns_instead_of_crashing_on_missing_head(context, monkeypatch) -> None:
    patch_backbone(monkeypatch)
    logger = _Logger()
    ctx = context(FakeModels(meta_error=ConfigError("no head", context={"name": "hcaptcha-v1"})))
    ctx.logger = logger

    await HCaptchaSolver().prepare(ctx)

    assert [call[0] for call in logger.calls] == ["classification.head.warmup_failed"]
    assert logger.calls[0][1]["head"] == "hcaptcha-v1"


def test_grid_classifier_is_abstract_but_subclasses_carry_contracts() -> None:
    assert issubclass(HCaptchaSolver, GridClassifier)
    assert HCaptchaSolver.head.name == "hcaptcha-v1"
