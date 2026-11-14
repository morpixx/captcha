"""Kasada KPSDK endpoints.

Kasada's bootstrap script is served under ``/149e9513-01fa-4fb0-aad4-
566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/{p|fp}`` on the target
origin — the exact UUIDs come from the response headers (`x-kpsdk-*`).
"""

from __future__ import annotations

# Bootstrap + sensor paths. The host is always the target origin.
KPSDK_PATH_IPS: str = "/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/ips"
KPSDK_PATH_FP: str = "/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/fp"
KPSDK_PATH_TL: str = "/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/tl"

KPSDK_HEADERS: tuple[str, ...] = (
    "x-kpsdk-cd",
    "x-kpsdk-cr",
    "x-kpsdk-ct",
    "x-kpsdk-im",
    "x-kpsdk-v",
)

# Cookie we return on success.
KPSDK_COOKIE: str = "KP_UIDz"
