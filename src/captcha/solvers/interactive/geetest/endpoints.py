"""GeeTest (极验) wire endpoints for v3 and v4.

v3 is served by 'api.geetest.com' ('gettype.php' / 'get.php' / 'ajax.php'),
v4 by 'gcaptcha4.geetest.com' ('load' / 'verify').  Puzzle images live on
'static.geetest.com' for both generations, and both accept the legacy
'api_server' task field: 2captcha clients pass either a bare host
('api.geetest.com') or a full URL ('https://api.geetest.com'), sometimes with a
trailing slash, sometimes pointing at the newer 'gcaptcha4.geevisit.com' edge.

The v3 'register' step (site backend -> gt + challenge) happens before the task
reaches us; 'V3_REGISTER' is here for probes only and no solver calls it.
"""

from __future__ import annotations

from urllib.parse import urlparse

from captcha.domain.errors import ConfigError

V3_DEFAULT_API = "https://api.geetest.com"
V3_STATIC = "https://static.geetest.com"
V3_REGISTER = "/register-slide"  # site-side challenge registration; probe only
V3_GETTYPE = "/gettype.php"  # sdk js manifest for the gt; not needed for the answer
V3_GET = "/get.php"  # round metadata, and (2nd call) the shuffled images
V3_AJAX = "/ajax.php"  # round mode, then the verify POST

V4_DEFAULT_API = "https://gcaptcha4.geetest.com"
# Observed 2026-10 build host; same API, selected with api_server when a site
# pins it. Kept separate so the default stays the vendor-documented host.
V4_GEEVISIT_API = "https://gcaptcha4.geevisit.com"
V4_STATIC = "https://static.geetest.com"
V4_LOAD = "/load"  # lot_number, pow_detail, bg/slice paths, payload/process_token, pt
V4_VERIFY = "/verify"  # GET answer + w -> seccode / captcha_output

# Both generations answer the same 'callback=geetest_<ms>' contract.
CALLBACK_PREFIX = "geetest_"

# Client type the web widget sends in every capture we have.
CLIENT_TYPE = "web"

# GET /verify still speaks payload_protocol=1 in the 2026 build.
V4_PAYLOAD_PROTOCOL = "1"

# The only v3/v4 challenge family a CV slider can answer.
DEFAULT_RISK_TYPE = "slide"


def api_base(override: str | None, default: str) -> str:
    """Normalize the legacy 'api_server' field into a scheme://host base URL.

    Accepts a bare host, an absolute URL, or a URL with a trailing slash; an
    empty/absent override yields the generation default. A malformed override
    is a task-config error, never a silent fallback: a wrong host burns a solve
    and then looks like a solver bug.
    """
    raw = (override or "").strip()
    if not raw:
        return default
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    try:
        host = parsed.hostname
        parsed.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError:
        host = None
    if not host or any(ch.isspace() for ch in host):
        raise ConfigError(
            f"api_server is not a usable URL: {override!r}",
            context={"api_server": override},
        )
    return f"{parsed.scheme}://{parsed.netloc}"


def asset_url(base: str, path: str) -> str:
    """Join an image path from a challenge response onto the static host."""
    if "://" in path:
        return path
    return base.rstrip("/") + "/" + path.lstrip("/")
