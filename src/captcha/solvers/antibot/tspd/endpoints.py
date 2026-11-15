"""F5 Shape (TSPD) endpoints and cookie names.

Shape serves its client from a rotating customer prefix on the target origin
(`/TSPD/…`, `/__TS/…`, `…/main.min.js`) and leaves one of two cookie
families behind: `TSPD_101` (BIG-IP Bot Defense) or `TS01<hex>` (ASM proactive
bot defense). F5's own KBs document both prefixes and that customers may rename
them per deployment.

Which means: no constant URL and no constant cookie name. The path comes from
the payload (scraped by the client), the cookie is matched by prefix, and the
sensor POST is whatever the script itself attempted -- replayed by the solver.
"""

from __future__ import annotations

#: Cookie families, longest prefix first so `TSPD_101` is not shadowed by `TS`.
TSPD_COOKIE_PREFIXES: tuple[str, ...] = ("TSPD", "TS")

#: The cookie an answer can be reduced to when the deployment uses the default.
TSPD_PRIMARY_COOKIE: str = "TSPD_101"

#: Customer script prefixes seen in the wild; used only to recognise a shape
#: script when the payload carries a bare path instead of a full URL.
TSPD_SCRIPT_HINTS: tuple[str, ...] = ("/TSPD/", "/__TS/", "main.min.js", ".js?")


def is_ts_cookie(name: str) -> bool:
    return name.startswith(TSPD_COOKIE_PREFIXES)


__all__ = [
    "TSPD_COOKIE_PREFIXES",
    "TSPD_PRIMARY_COOKIE",
    "TSPD_SCRIPT_HINTS",
    "is_ts_cookie",
]
