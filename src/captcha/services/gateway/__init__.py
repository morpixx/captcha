"""FastAPI gateway — public 2captcha-compatible surface."""
from __future__ import annotations

from captcha.services.gateway.app import build_state, create_app

__all__ = ["build_state", "create_app"]
