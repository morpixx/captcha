"""CutCaptcha endpoints.

Simple slider puzzle; `api.cutcaptcha.com` fronts a per-apikey flow.
"""

from __future__ import annotations

API_HOST = "api.cutcaptcha.com"
GET_CHALLENGE = "https://api.cutcaptcha.com/api/v1/challenge"
VERIFY = "https://api.cutcaptcha.com/api/v1/verify"
