"""Binance bCAPTCHA2 endpoints.

Binance gates login/registration with a slider (upgraded to a BOX grid after
repeated failures) served from bapi/composite/v1/public/antibot/*. The
request carries the fingerprint headers the web client attaches through its
axios interceptor: bnc-uuid, csrftoken, device-info, captcha-sdk-version,
x-captcha-se, plus the trace pair. Verified against the public 2026 field
guide: getCaptcha returns sig, salt, path2, ek, captchaType, tag; the image
lives under https://bin.bnbstatic.com and the answer goes to
validateCaptcha as an encrypted data blob plus s.

The endpoint names and field set are volatile by design (undocumented API).
Parsing accepts the aliases we have seen in the wild; unknown shapes fail
loudly with the key list rather than guessing.
"""

from __future__ import annotations

API_HOST = "www.binance.com"
BASE_API = f"https://{API_HOST}"

#: Safe-action precheck that mints sessionId/validateId before the challenge.
#: Optional: clients that already ran it pass validateId in.
PRECHECK = f"{BASE_API}/bapi/accounts/v1/public/account/security/request/precheck"
GET_CAPTCHA = f"{BASE_API}/bapi/composite/v1/public/antibot/getCaptcha"
VALIDATE_CAPTCHA = f"{BASE_API}/bapi/composite/v1/public/antibot/validateCaptcha"
#: Older name for the same endpoint, kept so a stale caller does not break.
VERIFY = VALIDATE_CAPTCHA

#: Relative path2 values resolve against this host.
IMAGE_BASE = "https://bin.bnbstatic.com"
