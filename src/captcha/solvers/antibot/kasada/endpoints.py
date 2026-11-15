"""Kasada KPSDK wire constants.

The path prefix is the same on every tenant (2026-10 captures): two fixed UUIDs
followed by the endpoint. Only the query string and the version header change,
so the prefix is a constant and the per-deploy parts are discovered:

    <prefix>/ips.js?timestamp=...   challenge script served with the 429
    <prefix>/p.js                   the SDK script that holds x-kpsdk-v
    <prefix>/fp                     background fingerprint request
    <prefix>/tl                     POST the VM payload -> x-kpsdk-ct, x-kpsdk-st
    <prefix>/mfc                    -> x-kpsdk-fc, x-kpsdk-h
"""

from __future__ import annotations

#: Fixed UUID pair every Kasada deploy uses. Do not "discover" this prefix.
KPSDK_PREFIX: str = "/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3"

KPSDK_UUID_IPS: str = "149e9513-01fa-4fb0-aad4-566afd725d1b"
KPSDK_UUID_TENANT: str = "2d206a39-8ed7-437e-a3be-862e0f06eea3"

KPSDK_PATH_IPS: str = KPSDK_PREFIX + "/ips.js"
KPSDK_PATH_P: str = KPSDK_PREFIX + "/p.js"
KPSDK_PATH_FP: str = KPSDK_PREFIX + "/fp"
KPSDK_PATH_TL: str = KPSDK_PREFIX + "/tl"
KPSDK_PATH_MFC: str = KPSDK_PREFIX + "/mfc"

#: Headers the SDK owns. cd is minted per request and is single use.
HDR_CT: str = "x-kpsdk-ct"
HDR_CD: str = "x-kpsdk-cd"
HDR_ST: str = "x-kpsdk-st"
HDR_H: str = "x-kpsdk-h"
HDR_FC: str = "x-kpsdk-fc"
HDR_V: str = "x-kpsdk-v"
HDR_DT: str = "x-kpsdk-dt"
HDR_IM: str = "x-kpsdk-im"

KPSDK_HEADERS: tuple[str, ...] = (HDR_CT, HDR_CD, HDR_ST, HDR_H, HDR_FC, HDR_V, HDR_DT, HDR_IM)

#: Headers the VM hands to the /tl request (besides content-type).
TL_HEADERS: tuple[str, ...] = (HDR_CT, HDR_DT, HDR_IM)

#: The /tl body is the raw fingerprint blob, never form/JSON encoded.
TL_CONTENT_TYPE: str = "application/octet-stream"

#: Kasada answers a challenge with 429, not with a page or a token.
STATUS_CHALLENGE: int = 429

#: Cookie sample. Names are per-tenant, so nothing keys on this value.
KPSDK_COOKIE: str = "KP_UIDz"

#: Version shape carried by x-kpsdk-v, e.g. j-1.2.797.
VERSION_RE: str = r"j-\d+\.\d+\.\d+"
