"""Castle.io endpoints and wire names.

Castle is the odd one out among the engines here: `castle.js` creates the request
token **entirely client-side** and makes no network call at all
(docs.castle.io/docs/sdk-browser.md, 2026-08-31: "Castle.js won't make such
requests"). A token lives for 120 s, is bound to one request, and travels in the
request **body** as `castle_request_token` (the header form is the older
integration and can hit proxy header limits).

That leaves two things worth pinning here: where the SDK script comes from, and
the storage namespace the device id lives in -- `__cuid` by default, configurable
per integration.
"""

from __future__ import annotations

#: Monitoring endpoint of the older integration. The current SDK does not call
#: it, but deployments on the legacy build do; kept for the replay path.
CASTLE_COLLECT_URL: str = "https://d.castle.io/v1/c/mon"

#: Publishable key header (public, safe to log) and the token header form.
CASTLE_PK_HEADER: str = "x-castle-client-id"
CASTLE_TOKEN_HEADER: str = "X-Castle-Request-Token"

#: Body field the token is expected in. 2captcha clients read the token itself.
CASTLE_REQUEST_FIELD: str = "castle_request_token"

#: SDK script. Integrations load it from here; a task may override the URL.
CASTLE_SDK_URL: str = "https://cdn.castle.io/v1/castle.js"

#: localStorage namespace the device id is stored under (SDK default).
CASTLE_STORAGE_NAMESPACE: str = "__cuid"

#: A request token is valid for 120 s and for exactly one request.
CASTLE_TOKEN_TTL_S: int = 120

#: Public shape of a *mock* token (Castle's own test tooling):
#: `test|device:<id>|ip:1.2.3.4` -- not a bypass, just the documented format.
CASTLE_MOCK_PREFIX: str = "test|"

__all__ = [
    "CASTLE_COLLECT_URL",
    "CASTLE_MOCK_PREFIX",
    "CASTLE_PK_HEADER",
    "CASTLE_REQUEST_FIELD",
    "CASTLE_SDK_URL",
    "CASTLE_STORAGE_NAMESPACE",
    "CASTLE_TOKEN_HEADER",
    "CASTLE_TOKEN_TTL_S",
]
