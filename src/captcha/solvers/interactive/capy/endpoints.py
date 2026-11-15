"""Capy (capy.me) puzzle endpoints.

The host page loads `<origin>/puzzle/get_js/?k=<public key>`; that script talks
to the same origin for the puzzle, the drag answer, and the site-side check.
"""

from __future__ import annotations

# FILL: regional API origin. Devtools -> Network -> the `get_js` request: copy
# its origin. JP tenants are served by `jp.api.capy.me`, the global default is
# `api.capy.me`; the puzzle, answer and verify calls must hit the same origin as
# `get_js`, otherwise the challenge id is not recognised.
API_ORIGIN = "https://api.capy.me"

# FILL: exact paths, same Network panel. `get` returns the puzzle JSON,
# `answer` takes the drag and returns the signed answer string, `verify` is the
# site-side check (private key) and is not called from here.
GET_CHALLENGE = f"{API_ORIGIN}/puzzle/get/"
POST_ANSWER = f"{API_ORIGIN}/puzzle/answer/"
VERIFY = f"{API_ORIGIN}/puzzle/verify"
