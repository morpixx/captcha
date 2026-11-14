"""captcha-solver-interactive — 14 vendor-specific interactive captcha plugins.

Entry-point loader discovers solver classes at worker startup; importing this
package is NOT required. Direct re-exports here are convenience for tests and
for the registry's `extra_modules=` escape hatch.
"""

from __future__ import annotations

from ._base import InteractiveBase

__all__ = ["InteractiveBase"]
