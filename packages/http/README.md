# captcha-http

HTTP stealth core for the solver fleet. Thin wrapper over `curl_cffi` so every
outbound request matches a real browser TLS/HTTP2 fingerprint — Cloudflare,
Akamai, PerimeterX, DataDome and friends profile JA3/JA4 before they ever read
a header, so plain `httpx` dies at the handshake.

Why `curl-impersonate`: it re-orders the ClientHello extensions, forges the
ALPN/ALPS list, sets the right HTTP/2 SETTINGS and HPACK dynamic table size
per-browser build. Nothing in pure Python matches it.

Pick an impersonate string with `pick_impersonate("chrome", mobile=False)` — it
fuzzy-matches against `JA_PROFILES`. Defaults to `chrome124` for desktop,
`chrome124_android` for mobile. Rotate per-session, never per-request inside a
session: TLS resumption gives you away if you switch mid-flight.

`Client` is session-per-task — construct, use, drop. No shared state, no
connection pool leakage between solver tasks. Safe for 1000+ concurrent
in-flight calls; cost is one `AsyncSession` per task.
