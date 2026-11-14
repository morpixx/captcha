"""Prosopo Procaptcha endpoints.

Public docs: https://docs.prosopo.io
SDK client: `@prosopo/procaptcha`.
Protocol is open source under `prosopo/captcha` (TypeScript); the challenge
is a 3x3 grid-click with signed commitments on the Substrate chain.
"""

from __future__ import annotations

API_HOST = "api.prosopo.io"
GET_CHALLENGE = "https://api.prosopo.io/v1/prosopo/provider/client/captcha/image"
SUBMIT_IMAGE = "https://api.prosopo.io/v1/prosopo/provider/client/solution"
SERVER_STATUS = "https://api.prosopo.io/v1/prosopo/provider/status"
