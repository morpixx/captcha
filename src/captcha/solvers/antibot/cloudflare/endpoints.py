"""Cloudflare endpoint constants.

Cloudflare's challenge platform is served under the *target* origin at a
fixed path. The managed-challenge HTML carries three dynamic tokens we
reconstruct — call it the (`cvId`, `cType`, `cRay`) triplet — and
POST back to the orchestrate endpoint. The second, generate-JS endpoint
is scraped from the same HTML.

Turnstile runs on `challenges.cloudflare.com` instead and has its own
problem shape (sitekey + widget), so its endpoints are kept separate.
The widget's token-mint POST is listed for documentation only: its byte
schema is gated (docs/reverse-2026.md §3), which is why the solver escalates
to the browser lane instead of replaying it.
"""

from __future__ import annotations

# Managed challenge — relative to the target origin (same host as site_url).
CF_CHALLENGE_PLATFORM_PREFIX: str = "/cdn-cgi/challenge-platform"
CF_ORCHESTRATE_FMT: str = "{origin}/cdn-cgi/challenge-platform/h/{variant}/orchestrate/jsch/v1"
CF_FLOW_FMT: str = "{origin}/cdn-cgi/challenge-platform/h/{variant}/flow/ov1/{cvid}/{ctype}/{cray}"

# Turnstile — hosted on cloudflare.
TURNSTILE_ORIGIN: str = "https://challenges.cloudflare.com"
TURNSTILE_API_JS: str = "https://challenges.cloudflare.com/turnstile/v0/api.js"
TURNSTILE_HOST: str = "challenges.cloudflare.com"
# The unversioned loader 302s to (or embeds) /turnstile/v0/b/<hash>/api.js.
TURNSTILE_BUNDLE_RE: str = r"/turnstile/v0/b/([A-Za-z0-9_-]+)/api\.js"
# Widget document for one sitekey; `{variant}` is the challenge-platform build
# segment read out of the bundle (b/g today).
TURNSTILE_WIDGET_FMT: str = (
    "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/{variant}/turnstile/if/ov2/av0/{sitekey}"
)
# Token mint. No public capture of the request body exists; kept as a marker.
TURNSTILE_MINT_FMT: str = (
    "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/{variant}/turnstile/f/av0/rch/{segment}/{sitekey}"
)

# Challenge-platform variants rotate with Cloudflare releases.
DEFAULT_VARIANT: str = "b"
