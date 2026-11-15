"""reCAPTCHA solver family (v2 audio/grid and v3 score-based).

Submodules register their own solvers via `@register_solver`; importing the
package alone must not register anything, because `score_based.recaptcha_v3`
shares the protocol helpers in `_common` / `_proto` without wanting the v2
solvers loaded. The lazy exports below keep `from captcha.solvers.recaptcha
import RecaptchaV2AudioSolver` working for callers and tests.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "RecaptchaV2AudioSolver",
    "RecaptchaV2EnterpriseSolver",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from .v2_audio import RecaptchaV2AudioSolver, RecaptchaV2EnterpriseSolver

        return {
            "RecaptchaV2AudioSolver": RecaptchaV2AudioSolver,
            "RecaptchaV2EnterpriseSolver": RecaptchaV2EnterpriseSolver,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
