"""reCAPTCHA v2 solver: audio bypass primary, grid delegation by payload.

One solver answers all four v2 types:

    RECAPTCHA_V2             checkbox, `size=normal`
    RECAPTCHA_V2_INVISIBLE   `size=invisible`
    RECAPTCHA_V2_CALLBACK    invisible callback variant
    RECAPTCHA_V2_ENTERPRISE  enterprise endpoint family

Dispatch is payload-driven: a task carrying `image_b64` + `prompt` is a
pre-extracted grid and goes to `solvers.classification.recaptcha_grid`; every
other task walks the audio pipeline in `_audio.py`. Both paths are HTTP-only
and need no browser or Node runtime.
"""

from __future__ import annotations

from typing import ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.solvers import Solver, SolverContext, SolverInfo, register_solver

from ._audio import solve_audio
from ._common import API2, API_VERSION, ENTERPRISE, Endpoints
from .v2_grid import is_grid_request, solve_grid

_INVISIBLE_TYPES = frozenset(
    {
        CaptchaType.RECAPTCHA_V2_INVISIBLE,
        CaptchaType.RECAPTCHA_V2_CALLBACK,
    }
)
_SIZES = frozenset({"normal", "invisible", "compact"})


class _RecaptchaV2Base(Solver):
    _ENDPOINTS: ClassVar[Endpoints] = API2
    _DEFAULT_SIZE: ClassVar[str] = "normal"

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = dict(task.payload or {})
        if is_grid_request(payload):
            return await solve_grid(task, ctx)
        return await solve_audio(
            task,
            ctx,
            endpoints=self._ENDPOINTS,
            anchor_size=self._anchor_size(task, payload),
        )

    def _anchor_size(self, task: Task, payload: dict[str, object]) -> str:
        override = payload.get("size")
        if isinstance(override, str) and override in _SIZES:
            return override
        if task.type in _INVISIBLE_TYPES:
            return "invisible"
        return self._DEFAULT_SIZE


@register_solver
class RecaptchaV2AudioSolver(_RecaptchaV2Base):
    """v2 checkbox, invisible and callback — audio bypass, grid on demand."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="recaptcha_v2_audio",
        types=(
            CaptchaType.RECAPTCHA_V2,
            CaptchaType.RECAPTCHA_V2_INVISIBLE,
            CaptchaType.RECAPTCHA_V2_CALLBACK,
        ),
        http=True,
        needs_js_runtime=False,
        experimental=False,
        version="0.2.0",
    )


@register_solver
class RecaptchaV2EnterpriseSolver(_RecaptchaV2Base):
    """Enterprise v2 — same pipeline, `/recaptcha/enterprise/` endpoints."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="recaptcha_v2_enterprise_audio",
        types=(CaptchaType.RECAPTCHA_V2_ENTERPRISE,),
        http=True,
        needs_js_runtime=False,
        experimental=False,
        version="0.2.0",
    )

    _ENDPOINTS = ENTERPRISE


__all__ = [
    "API_VERSION",
    "RecaptchaV2AudioSolver",
    "RecaptchaV2EnterpriseSolver",
]
