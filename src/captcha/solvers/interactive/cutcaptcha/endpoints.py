"""CutCaptcha endpoints.

Simple slider puzzle; `api.cutcaptcha.com` fronts a per-apikey flow. The
challenge call is scoped by the embedding host, the verify call by the
challenge id it returned, and the answer is the drag offset plus the pointer
track the vendor replays before it mints a token.

Both paths below are the shapes the public integrations use; they were not
captured from a live tenant in this repo, hence `experimental=True` on the
solver (see `docs/reverse-2026.md` for what "experimental" means here).
"""

from __future__ import annotations

API_HOST = "api.cutcaptcha.com"
GET_CHALLENGE = "https://api.cutcaptcha.com/api/v1/challenge"
VERIFY = "https://api.cutcaptcha.com/api/v1/verify"

#: Replies that mean the challenge is gone rather than the offset wrong.
EXPIRED_MARKERS: tuple[str, ...] = ("expire", "timeout", "invalid challenge", "not found", "过期")

#: The vendor rejects a track shorter than this (it replays the pointer path).
MIN_TRACK_POINTS = 12

#: A drag is horizontal in practice; keep a small vertical drift so the track is
#: not a perfectly straight line, which is what the track check is looking for.
TRACK_DRIFT_PX = 3.0
