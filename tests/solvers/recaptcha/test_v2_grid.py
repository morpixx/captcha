from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ConfigError, UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers.base import SolverContext
from captcha.solvers.classification.recaptcha_grid import RecaptchaGridSolver
from captcha.solvers.recaptcha.v2_audio import RecaptchaV2AudioSolver

SITEKEY = "test-sitekey"
SITE_URL = "https://example.com/login"


def make_task(payload: dict[str, Any] | None = None) -> Task:
    return Task(
        id=new_task_id(),
        type=CaptchaType.RECAPTCHA_V2,
        payload=payload or {},
        sitekey=SITEKEY,
        site_url=SITE_URL,
    )


async def test_grid_payload_delegates_to_classification_solver() -> None:
    grid_result = SolverResult(indices=[0, 4], extra={"head": "recaptcha-v1"})
    with patch.object(RecaptchaGridSolver, "solve_http", new=AsyncMock(return_value=grid_result)) as solve:
        result = await RecaptchaV2AudioSolver().solve_http(
            make_task({"image_b64": "ZmFrZQ==", "prompt": "bus"}),
            SolverContext(),
        )

    assert solve.await_count == 1
    delegated = solve.await_args.args[0]
    assert delegated.type is CaptchaType.CLASSIFICATION_GRID
    assert delegated.payload["prompt"] == "bus"
    assert result.indices == [0, 4]
    assert result.extra == {"head": "recaptcha-v1", "indices": [0, 4]}


async def test_grid_payload_without_prompt_is_unsolvable() -> None:
    with patch.object(RecaptchaGridSolver, "solve_http", new=AsyncMock()) as solve, pytest.raises(UnsolvableError):
        await RecaptchaV2AudioSolver().solve_http(
            make_task({"image_b64": "ZmFrZQ=="}),
            SolverContext(),
        )
    assert solve.await_count == 0


async def test_classification_unavailable_is_config_error() -> None:
    import sys

    saved = sys.modules.get("captcha.solvers.classification.recaptcha_grid")
    sys.modules["captcha.solvers.classification.recaptcha_grid"] = None  # type: ignore[assignment]
    try:
        with pytest.raises(ConfigError):
            await RecaptchaV2AudioSolver().solve_http(
                make_task({"image_b64": "ZmFrZQ==", "prompt": "bus"}),
                SolverContext(),
            )
    finally:
        if saved is not None:
            sys.modules["captcha.solvers.classification.recaptcha_grid"] = saved
        else:
            sys.modules.pop("captcha.solvers.classification.recaptcha_grid", None)


async def test_no_image_does_not_take_grid_path() -> None:
    with pytest.raises(ConfigError):
        await RecaptchaV2AudioSolver().solve_http(
            make_task({"prompt": "bus"}),
            SolverContext(http=None),
        )
