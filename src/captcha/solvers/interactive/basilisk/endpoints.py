"""Basilisk Captcha endpoints.

Ad-funded captcha; proof is "click the <company logo>". The widget loads
from `cdn.basiliskcaptcha.com` and verifies against `api.basiliskcaptcha.com`.
"""

from __future__ import annotations

API_HOST = "api.basiliskcaptcha.com"
CHALLENGE = "https://api.basiliskcaptcha.com/v1/challenge"
VERIFY = "https://api.basiliskcaptcha.com/v1/verify"
