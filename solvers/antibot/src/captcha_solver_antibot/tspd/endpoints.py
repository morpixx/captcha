"""F5 Shape (TSPD) endpoints.

Shape serves the challenge script as `main.min.js` under a rotating
customer prefix on the target origin; the integrity cookie is
``TS<hex>``. Payload POSTs to a per-customer `${prefix}/${hash}` path.
"""

from __future__ import annotations

# FILL: dump the per-tenant script prefix (varies: /TSPD/, /__TS/, /int/...)
TSPD_INTEGRITY_COOKIE_PREFIX: str = "TS"
TSPD_POST_PATH_HINT: str = "/TSPD"
