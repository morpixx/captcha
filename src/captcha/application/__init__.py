"""Use cases: the only orchestration in the system.

Depends on domain ports, never on adapters. Each module here answers one
question ("how does a task get created", "how does a worker solve one") and
can be unit-tested with fake ports.
"""

from __future__ import annotations

from .pingback import pingback_payload

__all__ = ["pingback_payload"]
