"""Fingerprint profile storage. The profile struct itself lives in the domain."""

from __future__ import annotations

from captcha.domain.profile import Profile

from .bundles import DEFAULT_BUNDLES, Bundle
from .store import Store, pick_profile

__all__ = [
    "Bundle",
    "DEFAULT_BUNDLES",
    "Profile",
    "Store",
    "pick_profile",
]
