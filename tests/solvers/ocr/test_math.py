from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from captcha.domain import CaptchaType, SolverResult
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ModelInferenceError
from captcha.domain.task import Task, new_task_id
from captcha.solvers.base import SolverContext
from captcha.solvers.ocr.math_image import MathImageSolver, _sanitize


def _mk_task() -> Task:
    return Task(
        id=new_task_id(),
        type=CaptchaType.MATH_IMAGE,
        payload={"body": "AA=="},  # dummy, OCR mocked
    )


def _fake_ocr(text: str) -> Any:
    async def _run(_task: Task, _ctx: SolverContext) -> SolverResult:
        return SolverResult(text=text, solve_mode=SolveMode.HTTP, latency_ms=1.0)

    return _run


def test_sanitize_strips_noise() -> None:
    assert _sanitize("2 + 3 = ?") == "2+3"
    assert _sanitize("4×5") == "4*5"
    assert _sanitize("10−2") == "10-2"
    assert _sanitize("12 ÷ 4") == "12/4"


def test_math_add() -> None:
    solver = MathImageSolver()
    with patch.object(solver._ocr, "solve_http", side_effect=_fake_ocr("2 + 3 = ?")):
        result = asyncio.run(solver.solve_http(_mk_task(), SolverContext()))
    assert result.text == "5"


def test_math_mul_with_ocr_confusion() -> None:
    solver = MathImageSolver()
    # OCR saw '×' as 'x'
    with patch.object(solver._ocr, "solve_http", side_effect=_fake_ocr("7 x 8")):
        result = asyncio.run(solver.solve_http(_mk_task(), SolverContext()))
    assert result.text == "56"


def test_math_div_integer_result() -> None:
    solver = MathImageSolver()
    with patch.object(solver._ocr, "solve_http", side_effect=_fake_ocr("12 / 4")):
        result = asyncio.run(solver.solve_http(_mk_task(), SolverContext()))
    assert result.text == "3"


def test_math_fails_on_garbage() -> None:
    solver = MathImageSolver()
    with (
        patch.object(solver._ocr, "solve_http", side_effect=_fake_ocr("hello world")),
        pytest.raises(ModelInferenceError),
    ):
        asyncio.run(solver.solve_http(_mk_task(), SolverContext()))


def test_math_empty_ocr_raises() -> None:
    solver = MathImageSolver()
    with patch.object(solver._ocr, "solve_http", side_effect=_fake_ocr("")), pytest.raises(ModelInferenceError):
        asyncio.run(solver.solve_http(_mk_task(), SolverContext()))
