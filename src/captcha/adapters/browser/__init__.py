"""Browser fallback pool — camoufox / patchright / playwright."""

from __future__ import annotations

from .pool import Pool
from .session import Session, SessionConfig

__all__ = ["Pool", "Session", "SessionConfig"]
