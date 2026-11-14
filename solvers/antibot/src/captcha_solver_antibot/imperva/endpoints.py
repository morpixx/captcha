"""Imperva (Incapsula) endpoints.

Imperva's JS interstitial POSTs to the target origin at a resource path
derived from the challenge seed (``/_Incapsula_Resource?SWKMTFSR=1&...``).
The exact path is scraped from the challenge HTML; we only wire the
common prefix here.
"""

from __future__ import annotations

INCAPSULA_RESOURCE_PATH: str = "/_Incapsula_Resource"

# Query keys that appear on the challenge POST. The client script adds a
# per-session nonce (`SWSSFN`) we lift out of the challenge HTML.
# FILL: SWSSFN extraction regex lives in captcha_sensors.imperva.
IMPERVA_COOKIES: tuple[str, ...] = (
    "visid_incap",
    "incap_ses",
    "nlbi",
    "___utmvc",
)
