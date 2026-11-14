"""CaptchaFox endpoints.

Public docs: https://docs.captchafox.com
SDK bundle: `https://cdn.captchafox.com/api.js` -> `api.captchafox.com`.
Challenge is a rotated-piece puzzle (align target angle) or 3-point click.
"""

from __future__ import annotations

API_HOST = "api.captchafox.com"
INIT = "https://api.captchafox.com/v1/challenge"
VERIFY = "https://api.captchafox.com/v1/verify"
