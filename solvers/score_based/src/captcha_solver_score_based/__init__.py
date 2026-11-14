"""Score-based token solvers.

Importing this package registers the three bundled solvers with the global
`SolverRegistry` via their entry-point class decorators.
"""

from captcha_solver_score_based.recaptcha_v3 import (
    RecaptchaV3EnterpriseSolver,
    RecaptchaV3Solver,
)
from captcha_solver_score_based.turnstile import TurnstileInvisibleSolver

__all__ = [
    "RecaptchaV3EnterpriseSolver",
    "RecaptchaV3Solver",
    "TurnstileInvisibleSolver",
]
