"""Binance antibot captcha endpoints.

Binance serves a slider puzzle via `/bapi/composite/v1/public/antibot/*`.
Requests require `bnc-uuid` (per-tab cookie) and a `csrftoken` header.
Trace headers include `fvideo-id`, `x-trace-id`, `x-ui-request-trace`.
"""

from __future__ import annotations

API_HOST = "www.binance.com"
GET_CAPTCHA = "https://www.binance.com/bapi/composite/v1/public/antibot/getCaptcha"
VERIFY = "https://www.binance.com/bapi/composite/v1/public/antibot/verify"
