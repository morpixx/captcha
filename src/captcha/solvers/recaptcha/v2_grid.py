"""reCAPTCHA v2 grid path — delegate, never re-implement.

Some flows already have the challenge tiles (a browser worker scrapes them, or
a client posts `image_b64` + `prompt`). Classifying those tiles is the job of
`solvers.classification.recaptcha_grid.RecaptchaGridSolver`; duplicating its
head/backbone logic here would fork the model contract. This module only
adapts a `RECAPTCHA_V2*` task into a `CLASSIFICATION_GRID` task and passes the
result back.

The v2 grid result is returned as `indices` (and mirrored into `extra`) so a
caller that asked for tile selection can use it. It is not a substitute for
the final `g-recaptcha-response` token — the audio path or a browser flow
still has to submit the selection.
"""

from __future__ import annotations

import msgspec

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ConfigError, UnsolvableError
from captcha.solvers import SolverContext


def is_grid_request(payload: dict[str, object]) -> bool:
    """True when the task carries a pre-extracted grid instead of a token task."""
    return bool(payload.get("image_b64") or payload.get("image"))


async def solve_grid(task: Task, ctx: SolverContext) -> SolverResult:
    payload = dict(task.payload or {})
    if not is_grid_request(payload):
        raise UnsolvableError(
            "reCAPTCHA grid path requires payload['image_b64']",
            context={"task": str(task.id)},
        )
    prompt = payload.get("prompt") or payload.get("question") or payload.get("target")
    if not prompt:
        raise UnsolvableError(
            "reCAPTCHA grid path requires payload['prompt']",
            context={"task": str(task.id)},
        )

    try:
        from captcha.solvers.classification.recaptcha_grid import RecaptchaGridSolver
    except Exception as exc:
        raise ConfigError(
            "classification grid solver is not importable — install --extra cv",
            context={"error": f"{type(exc).__name__}: {exc}"},
        ) from exc

    grid_task = msgspec.structs.replace(
        task,
        type=CaptchaType.CLASSIFICATION_GRID,
        payload=payload,
    )
    result = await RecaptchaGridSolver().solve_http(grid_task, ctx)

    extra = dict(result.extra or {})
    if result.indices is not None:
        extra.setdefault("indices", result.indices)
    return SolverResult(
        indices=result.indices,
        extra=extra,
        solve_mode=result.solve_mode,
        latency_ms=result.latency_ms,
    )
