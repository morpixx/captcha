"""Score-based token solvers.

Importing this package registers the two bundled reCAPTCHA v3 solvers with the
global `SolverRegistry` via their entry-point class decorators.

Turnstile used to live here as a second, HTTP-only stub for
TURNSTILE_INVISIBLE while the antibot package had its own. One captcha type is
one solver file (AGENTS.md golden rule 8), and the working implementation needs
both the browser-free bootstrap and the browser lane, so the duplicate was
removed in favour of `captcha.solvers.antibot.cloudflare.turnstile`.
"""

from captcha.solvers.score_based.recaptcha_v3 import (
    RecaptchaV3EnterpriseSolver,
    RecaptchaV3Solver,
)

__all__ = [
    "RecaptchaV3EnterpriseSolver",
    "RecaptchaV3Solver",
]
