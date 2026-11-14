"""Arkose Labs FunCaptcha solver family.

Importing this package registers:
    * FunCaptchaSolver — base + enterprise variants (via subclass)
    * FunCaptchaBDASolver — BDA-only token path
"""

from captcha_solver_funcaptcha.bda import FunCaptchaBDASolver
from captcha_solver_funcaptcha.funcaptcha import (
    FunCaptchaEnterpriseSolver,
    FunCaptchaSolver,
)

__all__ = [
    "FunCaptchaBDASolver",
    "FunCaptchaEnterpriseSolver",
    "FunCaptchaSolver",
]
