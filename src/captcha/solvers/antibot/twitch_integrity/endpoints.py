"""Twitch Integrity endpoints.

Twitch gates its GQL and streaming API behind an integrity token minted
at ``https://gql.twitch.tv/integrity``. Token lifetime is short; solvers
return the raw JWT plus the ``expires_in`` so the caller can refresh in
time.
"""

from __future__ import annotations

INTEGRITY_URL: str = "https://gql.twitch.tv/integrity"

# Client-ID header the Twitch web frontend uses — rotates rarely.
WEB_CLIENT_ID: str = "kimne78kx3ncx6brgo4mv6wki5h1ko"

# Device-ID / Session-ID are task-provided (user-session tokens).
INTEGRITY_REQUIRED_HEADERS: tuple[str, ...] = (
    "client-id",
    "x-device-id",
    "client-session-id",
    "client-version",
)
