"""Cloudflare endpoint constants.

Cloudflare's challenge platform is served under the *target* origin at a
fixed path. The managed-challenge HTML carries three dynamic tokens we
reconstruct — call it the (``cvId``, ``cType``, ``cRay``) triplet — and
POST back to the orchestrate endpoint. The second, generate-JS endpoint
is scraped from the same HTML.

Turnstile runs on `challenges.cloudflare.com` instead and has its own
problem shape (sitekey + widget), so its endpoints are kept separate.
"""

from __future__ import annotations

# Managed challenge — relative to the target origin (same host as site_url).
CF_CHALLENGE_PLATFORM_PREFIX: str = "/cdn-cgi/challenge-platform"
CF_ORCHESTRATE_FMT: str = "{origin}/cdn-cgi/challenge-platform/h/{variant}/orchestrate/jsch/v1"
CF_FLOW_FMT: str = "{origin}/cdn-cgi/challenge-platform/h/{variant}/flow/ov1/{cvid}/{ctype}/{cray}"

# Turnstile — hosted on cloudflare.
TURNSTILE_ORIGIN: str = "https://challenges.cloudflare.com"
TURNSTILE_SITEKEY_API: str = "https://challenges.cloudflare.com/turnstile/v0/api.js"
# FILL: siteverify-adjacent endpoint for headless flow (/turnstile/v0/g/ or /turnstile/v0/t/?)
TURNSTILE_WIDGET_FMT: str = "https://challenges.cloudflare.com/turnstile/v0/{variant}/{sitekey}"

# Challenge-platform variants rotate with Cloudflare releases.
# FILL: dump {variant} segments from a captured session; cache per site.
DEFAULT_VARIANT: str = "b"
