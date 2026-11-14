"""reCAPTCHA v2 audio-bypass solver family.

Importing this package registers both the v2 and v2-enterprise solvers via
their `@register_solver` decorators.
"""

from captcha.solvers.recaptcha.v2_audio import (
    RecaptchaV2AudioSolver,
    RecaptchaV2EnterpriseSolver,
)

__all__ = [
    "RecaptchaV2AudioSolver",
    "RecaptchaV2EnterpriseSolver",
]
