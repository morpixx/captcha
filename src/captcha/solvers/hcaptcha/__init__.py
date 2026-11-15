"""hCaptcha pack: the browser-lane solver and the site profiles it reads.

The grid classifier lives in captcha.solvers.classification.hcaptcha and is
registered separately; this package is the browser fallback for the same
CaptchaType values.
"""

from __future__ import annotations

from .browser import HCaptchaBrowserSolver
from .common import (
    EXECUTE_JS,
    MIN_TOKEN_LEN,
    TOKEN_PREFIXES,
    capture_resp_key,
    capture_user_agent,
    challenge_shown,
    normalize_token,
    parse_cookies,
    token_problem,
    truthy,
    validate_token,
)
from .sites import (
    DEFAULT_MINT_URL,
    Site,
    all_sites,
    find_site,
    host_matches,
    host_of,
    register_site,
    resolve_site,
)

__all__ = [
    "DEFAULT_MINT_URL",
    "EXECUTE_JS",
    "MIN_TOKEN_LEN",
    "TOKEN_PREFIXES",
    "HCaptchaBrowserSolver",
    "Site",
    "all_sites",
    "capture_resp_key",
    "capture_user_agent",
    "challenge_shown",
    "find_site",
    "host_matches",
    "host_of",
    "normalize_token",
    "parse_cookies",
    "register_site",
    "resolve_site",
    "token_problem",
    "truthy",
    "validate_token",
]
