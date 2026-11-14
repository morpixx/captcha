"""BotDeflector endpoints.

Served under the target origin at a per-tenant JS path; the collected
signals POST to a sibling endpoint on the same host.
"""

from __future__ import annotations

# FILL: dump the real per-tenant prefix (seen so far: `/__bd/`, `/bd-collect/`).
BOTDEFLECTOR_PATH_HINT: str = "/__bd/collect"
BOTDEFLECTOR_COOKIE: str = "bd_token"
