"""Prosopo Procaptcha endpoints.

Prosopo's provider API is open source (`prosopo/captcha`) but the client half
is not a token endpoint a solver can call with a sitekey: the widget creates a
self-custodied SR25519 user, signs a timestamp and the provider request hash,
and submits a signed commitment. The provider never returns a token to a bare
HTTP client — the `procaptcha-response` value is produced by the widget.

What remains useful over HTTP is the provider surface itself (status and the
challenge routes below), so a preflight can tell a dead provider from a bad
sitekey. The token path lives in `solver.solve_browser`.
"""

from __future__ import annotations

API = "https://api.prosopo.io"
CHALLENGE = API + "/v1/prosopo/provider/client/captcha/image"
SOLUTION = API + "/v1/prosopo/provider/client/solution"
STATUS = API + "/v1/prosopo/provider/status"
#: Server-side verification (the *site's* secret, never the solver's).
SITEVERIFY = API + "/siteverify"
