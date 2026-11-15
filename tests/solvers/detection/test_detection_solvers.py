"""Click / bbox / drag / draw solvers on the unified detector interface."""

from __future__ import annotations

import uuid

import msgspec
import pytest
from _detection_support import FakeDetector, install_fake_detector, png_b64, solver_context

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.domain.solution import build_solution
from captcha.solvers.detection.bbox import BBoxSolver
from captcha.solvers.detection.click import ClickSolver
from captcha.solvers.detection.drag import DragDropSolver
from captcha.solvers.detection.draw import DrawAroundSolver

BOXES = [[40, 50, 120, 110], [200, 20, 280, 90]]
SCORES = [0.9, 0.4]
CLASSES = [0, 1]


def _task(captcha_type: CaptchaType, **payload: object) -> Task:
    return Task(id=uuid.uuid4(), type=captcha_type, payload=dict(payload))


def _detector(
    *,
    boxes: object = BOXES,
    scores: object = SCORES,
    classes: object = CLASSES,
    class_names: tuple[str, ...] = ("bus", "crosswalk", "traffic light"),
) -> FakeDetector:
    return FakeDetector(boxes=boxes, scores=scores, classes=classes, class_names=class_names)


async def test_click_returns_centre_of_best_target_box(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _detector()
    install_fake_detector(monkeypatch, fake)

    result = await ClickSolver().solve_http(
        _task(CaptchaType.CLICK, image=png_b64(), target="bus"),
        solver_context(),
    )

    assert result.coords == [(80.0, 80.0)]
    assert result.extra is not None
    assert result.extra["confidence"] == pytest.approx(0.9)
    assert result.extra["n_detections"] == 1  # filtered to the requested class
    assert fake.calls[0]["classes"] == [0]
    solution = build_solution(CaptchaType.CLICK, msgspec.structs.asdict(result))
    assert solution is not None
    assert solution["coordinates"] == [(80.0, 80.0)]


async def test_bbox_returns_xywh(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_detector(monkeypatch, _detector(scores=[0.4, 0.9]))

    result = await BBoxSolver().solve_http(
        _task(CaptchaType.BOUNDING_BOX, image=png_b64(), target="crosswalk"),
        solver_context(),
    )

    assert result.bbox == (200.0, 20.0, 80.0, 70.0)
    solution = build_solution(CaptchaType.BOUNDING_BOX, msgspec.structs.asdict(result))
    assert solution is not None
    assert solution["bbox"] == [200.0, 20.0, 80.0, 70.0]


async def test_draw_returns_box_polygon(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_detector(
        monkeypatch,
        _detector(boxes=[[30, 60, 90, 130], [200, 20, 280, 90]], scores=[0.8, 0.3], classes=[2, 1]),
    )

    result = await DrawAroundSolver().solve_http(
        _task(CaptchaType.DRAW_AROUND, image=png_b64(), target="traffic light"),
        solver_context(),
    )

    assert result.coords == [(30.0, 60.0), (90.0, 60.0), (90.0, 130.0), (30.0, 130.0)]
    assert result.extra is not None
    assert result.extra["source"] == "bbox_rect"
    solution = build_solution(CaptchaType.DRAW_AROUND, msgspec.structs.asdict(result))
    assert solution is not None and len(solution["coordinates"]) == 4


async def test_drag_returns_two_centres_and_trajectory(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_detector(monkeypatch, _detector())

    result = await DragDropSolver().solve_http(
        _task(
            CaptchaType.DRAG_DROP,
            image=png_b64(),
            source_target="bus",
            dest_target="crosswalk",
            trajectory_points=5,
            seed=7,
        ),
        solver_context(),
    )

    assert result.coords == [(80.0, 80.0), (240.0, 55.0)]
    assert result.extra is not None
    trajectory = result.extra["trajectory"]
    assert len(trajectory) == 5
    assert all(len(point) == 3 for point in trajectory)


async def test_missing_payload_fields_are_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="missing 'image'"):
        await ClickSolver().solve_http(_task(CaptchaType.CLICK, target="bus"), solver_context())
    with pytest.raises(UnsolvableError, match="missing 'target'"):
        await BBoxSolver().solve_http(_task(CaptchaType.BOUNDING_BOX, image=png_b64()), solver_context())
    with pytest.raises(UnsolvableError, match="source_target"):
        await DragDropSolver().solve_http(_task(CaptchaType.DRAG_DROP, image=png_b64()), solver_context())
    with pytest.raises(UnsolvableError, match="missing 'target'"):
        await DrawAroundSolver().solve_http(_task(CaptchaType.DRAW_AROUND, image=png_b64()), solver_context())


async def test_bad_confidence_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_detector(monkeypatch, _detector())
    with pytest.raises(UnsolvableError, match="conf"):
        await ClickSolver().solve_http(
            _task(CaptchaType.CLICK, image=png_b64(), target="bus", conf="loud"),
            solver_context(),
        )


async def test_unknown_target_is_unsolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_detector(monkeypatch, _detector())
    with pytest.raises(UnsolvableError, match="not found"):
        await ClickSolver().solve_http(
            _task(CaptchaType.CLICK, image=png_b64(), target="dragon"),
            solver_context(),
        )


async def test_no_detections_is_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_detector(monkeypatch, _detector(boxes=[], scores=[], classes=[]))
    with pytest.raises(ModelInferenceError, match="no detections"):
        await ClickSolver().solve_http(
            _task(CaptchaType.CLICK, image=png_b64(), target="bus"),
            solver_context(),
        )


async def test_drag_coincident_targets_is_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_detector(
        monkeypatch,
        _detector(boxes=[[10, 10, 20, 20], [10, 10, 20, 20]], scores=[0.9, 0.8], classes=[0, 1]),
    )
    with pytest.raises(ModelInferenceError, match="coincide"):
        await DragDropSolver().solve_http(
            _task(CaptchaType.DRAG_DROP, image=png_b64(), source_target="bus", dest_target="crosswalk"),
            solver_context(),
        )


async def test_drag_rejects_bad_trajectory_options(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_detector(monkeypatch, _detector())
    with pytest.raises(UnsolvableError, match="trajectory_points"):
        await DragDropSolver().solve_http(
            _task(
                CaptchaType.DRAG_DROP,
                image=png_b64(),
                source_target="bus",
                dest_target="crosswalk",
                trajectory_points=1,
            ),
            solver_context(),
        )
    with pytest.raises(UnsolvableError, match="seed"):
        await DragDropSolver().solve_http(
            _task(
                CaptchaType.DRAG_DROP,
                image=png_b64(),
                source_target="bus",
                dest_target="crosswalk",
                seed="abc",
            ),
            solver_context(),
        )


async def test_missing_registry_is_config_error() -> None:
    with pytest.raises(ConfigError, match=r"requires ctx\.models"):
        await ClickSolver().solve_http(
            _task(CaptchaType.CLICK, image=png_b64(), target="bus"),
            solver_context(models=None),
        )


def test_solver_inventory_covers_detection_types() -> None:
    declared = set()
    for solver in (ClickSolver, BBoxSolver, DragDropSolver, DrawAroundSolver):
        assert solver.info.http is True
        declared.update(solver.info.types)
    assert declared == {
        CaptchaType.CLICK,
        CaptchaType.COORDINATES,
        CaptchaType.BOUNDING_BOX,
        CaptchaType.DRAG_DROP,
        CaptchaType.DRAW_AROUND,
    }
