"""hCaptcha hsw/hsj bundle worker: a Node process plus the Python runner for it.

The bundle needs a browser-shaped realm (jsdom + canvas) and the aes-gcm it
exposes for the encrypted getcaptcha body, so it runs in its own Node process
started once per worker. The solver reaches it through ctx.extra["hcaptcha_js"].
"""

from __future__ import annotations

from .runner import JsBundleRunner

__all__ = ["JsBundleRunner"]
