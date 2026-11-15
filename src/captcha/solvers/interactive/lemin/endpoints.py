"""Lemin (leminnow) cropped-captcha endpoints.

The widget asks the dashboard for a challenge, then talks to `api.leminnow.com`
for the answer check.
"""

from __future__ import annotations

# FILL: challenge bootstrap. Devtools -> Network -> the first request the lemin
# script makes after load (host `dashboard.leminnow.com`): copy the URL, the JSON
# body and the reply's field names for the challenge id plus the scene/piece URLs.
CHALLENGE = "https://dashboard.leminnow.com/api/v1/captcha/challenge"

API_BASE = "https://api.leminnow.com/captcha/v1/cropped"
# FILL: pre-validate path. Network -> the POST made after a manual solve: copy
# the URL and confirm the body is the JSON `{answer, challenge_id}` pair.
PRE_VALIDATE = f"{API_BASE}/pre-validate"
# Site-side check the host backend runs with its private key; not called here.
VALIDATE = f"{API_BASE}/validate"

# Widget pointer-track geometry: 10 px snap grid, the piece's home position and
# the minimum accepted track length.
# FILL: solve one puzzle by hand and read `lemin_answer` from the form POST in
# Network: confirm the grid, the home `(x, y)` the track starts from and that
# every token is `<index>x<left>x<top>x` in base 32.
GRID = 10
HOME = (5, 405)
MIN_POINTS = 12
