"""Imperva (Incapsula) endpoints.

Imperva's JS interstitial POSTs back to the protected origin at a resource path
of its own (`/_Incapsula_Resource`), with a per-session nonce in the query. The
dynamic reese script URL is *not* a constant: it is scraped from the
interruption page and arrives in the task payload (Capsolver calls it
`reeseScriptURL` and marks it required).
"""

from __future__ import annotations

from urllib.parse import quote

INCAPSULA_RESOURCE_PATH: str = "/_Incapsula_Resource"

#: Query the client script appends; `e` is the per-session nonce.
INCAPSULA_QUERY: str = "SWKMTFSR=1&e={nonce}"

#: Cookie families the answer is built around. Prefix match, because Imperva
#: suffixes `incap_ses_<digits>` per customer (and per port on some builds).
IMPERVA_COOKIE_PREFIXES: tuple[str, ...] = ("visid_incap", "incap_ses", "nlbi", "reese84", "___utmvc")

#: The one cookie every Imperva answer can be reduced to.
IMPERVA_PRIMARY_COOKIE: str = "reese84"


def resource_url(origin: str, nonce: str) -> str:
    """Absolute sensor POST URL for a protected origin."""
    root = origin.rstrip("/")
    return f"{root}{INCAPSULA_RESOURCE_PATH}?{INCAPSULA_QUERY.format(nonce=quote(nonce, safe=''))}"


__all__ = [
    "IMPERVA_COOKIE_PREFIXES",
    "IMPERVA_PRIMARY_COOKIE",
    "INCAPSULA_QUERY",
    "INCAPSULA_RESOURCE_PATH",
    "resource_url",
]
