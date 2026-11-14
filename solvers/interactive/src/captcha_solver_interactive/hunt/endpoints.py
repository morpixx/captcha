"""Hunt (anti-bot game) captcha endpoints.

Vendor name varies by integrator; all variants share:
  - a per-session JWT delivered from the parent page
  - a WebSocket or long-poll for the "hunt" (click the <adjective> <noun>)
  - a verify POST that returns a token to the parent page

Hosts vary per integration. Record the integrator-specific base URL in the
task payload (`payload["host"]`); the endpoints below are the typical paths.
"""

from __future__ import annotations

PATH_INIT = "/api/hunt/init"
PATH_IMAGE = "/api/hunt/image"
PATH_VERIFY = "/api/hunt/verify"
