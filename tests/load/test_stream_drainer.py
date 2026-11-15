"""The external drainer: one plausible result per solution kind, no vendor.

The heavy paths (Redis streams, Postgres mirror) live behind the CLI and are
exercised against a real stack by `tests/integration`; what is unit-testable
here is the result mapping, which is what the gateway projects back to the
client.
"""

from __future__ import annotations

import uuid

from tools.load.stream_drainer import result_for

from captcha.domain.catalog import SolutionKind, solution_kind
from captcha.domain.enums import CaptchaType, SolveMode, TaskStatus
from captcha.domain.task import Task, TaskEnvelope


def _envelope(captcha_type: CaptchaType) -> TaskEnvelope:
    return TaskEnvelope(
        task=Task(id=uuid.uuid4(), type=captcha_type, payload={}),
        status=TaskStatus.PENDING,
    )


def test_text_type_gets_a_text_answer() -> None:
    result = result_for(_envelope(CaptchaType.TEXT))
    assert result.text == "loadtest"
    assert result.solve_mode is SolveMode.HTTP


def test_grid_type_gets_indices_not_text() -> None:
    result = result_for(_envelope(CaptchaType.CLASSIFICATION_GRID))
    assert result.indices == [0, 1]
    assert result.text is None


def test_geetest_v3_carries_the_vendor_keys() -> None:
    result = result_for(_envelope(CaptchaType.GEETEST_V3))
    assert result.token == "loadtest"
    assert result.extra == {"challenge": "loadtest", "validate": "loadtest"}


def test_every_type_produces_a_result_without_raising() -> None:
    for captcha_type in CaptchaType:
        result = result_for(_envelope(captcha_type))
        assert result.solve_mode is SolveMode.HTTP


def test_result_shape_follows_the_catalog() -> None:
    shapes = {
        SolutionKind.TEXT: lambda r: r.text is not None,
        SolutionKind.ROTATE: lambda r: r.angle is not None,
        SolutionKind.SLIDER: lambda r: r.offset is not None,
        SolutionKind.GRID: lambda r: r.indices is not None,
        SolutionKind.BBOX: lambda r: r.bbox is not None,
        SolutionKind.COORDS: lambda r: r.coords is not None,
        SolutionKind.GEETEST_V3: lambda r: r.token is not None and bool(r.extra),
    }
    checked = 0
    for captcha_type in CaptchaType:
        kind = solution_kind(captcha_type)
        check = shapes.get(kind) if kind is not None else None
        if check is None:
            continue
        assert check(result_for(_envelope(captcha_type))), captcha_type
        checked += 1
    # The rest of the enum is token/cookie shaped and answered by the default arm.
    assert checked >= 12, "the drainer lost its coverage of the catalog shapes"
