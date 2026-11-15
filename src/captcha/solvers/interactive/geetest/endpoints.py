"""GeeTest (极验) wire endpoints for v3 and v4.

v3 is served by `api.geetest.com` (`gettype.php` / `get.php` / `ajax.php`),
v4 by `gcaptcha4.geetest.com` (`load` / `verify`). Puzzle images live on
`static.geetest.com` for both generations, and both accept the legacy
`api_server` task field: 2captcha clients pass either a bare host
(`api.geetest.com`) or a full URL (`https://api.geetest.com`), sometimes with a
trailing slash, sometimes pointing at the newer `gcaptcha4.geevisit.com` edge.

Keep every URL constant here; solvers build from `api_base()` + the path
constants, so an `api_server` override cannot half-apply.
"""

from __future__ import annotations

from urllib.parse import urlparse

V3_DEFAULT_API = "https://api.geetest.com"
V3_STATIC = "https://static.geetest.com"
V3_GETTYPE = "/gettype.php"  # sdk js manifest for the gt; not needed for the answer
V3_GET = "/get.php"  # challenge metadata, and (2nd call) the shuffled images
V3_AJAX = "/ajax.php"  # round mode, then the verify POST

V4_DEFAULT_API = "https://gcaptcha4.geetest.com"
V4_STATIC = "https://static.geetest.com"
V4_LOAD = "/load"  # lot_number, pow_detail, bg/slice paths
V4_VERIFY = "/verify"  # answer + w -> seccode / captcha_output

# Both generations answer the same `callback=geetest_<ms>` contract.
CALLBACK_PREFIX = "geetest_"

# Client type the web widget sends in every capture we have.
CLIENT_TYPE = "web"


def api_base(override: str | None, default: str) -> str:
    """Normalize the legacy `api_server` field into a scheme://host base URL.

    Accepts a bare host, an absolute URL, or a URL with a trailing slash; an
    empty/absent override yields the generation default.
    """
    raw = (override or "").strip()
    if not raw:
        return default
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        # A garbage override must not silently fall back to the default: a wrong
        # host burns a solve and then looks like a solver bug.
        raise ValueError(f"api_server is not a usable URL: {override!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def asset_url(base: str, path: str) -> str:
    """Join an image path from a challenge response onto the static host."""
    if "://" in path:
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")
