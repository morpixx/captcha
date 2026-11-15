"""SSRF guard for client-supplied URLs at the gateway boundary (contract §8).

`pageurl`, `websiteURL`, `challenge_url`, `surl`, `api_server` and
`callbackUrl` are all fetched by a worker *after* the task is queued and charged,
so they are checked here before any money moves. The check is
`netguard.ensure_public_url_shape`: scheme, length, control characters, port,
denied hostnames (`localhost`, `*.localhost`, `metadata.google.internal`) and
IP literals — private, loopback, link-local, ULA, CGNAT, multicast and metadata
addresses are refused.

No DNS happens in this function. The create path is the hottest path in the
gateway and a blocking resolver call there buys little: the address a solver
connects to is decided later, so a hostname answer checked now can already be a
different answer then. The authoritative resolved-address check stays where the
connection is made (`adapters.browser.provider` on navigation) and is the
recommended next step for the HTTP transport and the pingback sender. Residual
risk — a hostname whose DNS answer is private space, and rebinding between
create and fetch — is written down in `docs/redteam-report.md`
(RT-01 / RT-02).

`UrlRejectedError` carries field/host/address and never the URL itself: a
`pageurl` may hold a token.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from captcha.adapters.netguard import ensure_public_url_shape
from captcha.domain.errors import UrlRejectedError

#: Payload keys that carry a URL (or a bare host) a solver may fetch. Wire
#: spellings from 2captcha/capsolver, the internal names the solvers read, and
#: the per-engine override keys that end up in a request URL (`host` is
#: FunCaptcha's surl fallback; `script_url`/`sbsd_url`/`tl_url` are fetched
#: script and payload URLs).
URL_FIELDS: tuple[str, ...] = (
    "pageurl",
    "page_url",
    "websiteURL",
    "websiteUrl",
    "site_url",
    "url",
    "challenge_url",
    "challengeUrl",
    "captcha_url",
    "surl",
    "subdomain",
    "api_server",
    "redirectUri",
    "redirect_uri",
    "host",
    "domain",
    "widget_url",
    "widgetUrl",
    "collect_url",
    "sbsd_url",
    "akam_url",
    "sensor_js_url",
    "script_url",
    "tl_url",
    "mfc_url",
    "ips_url",
    "p_js_url",
)

#: Fields that are a URL only when they already spell one out. A VK captcha can
#: arrive as a bare base64 blob here; that is not a URL and must not be parsed.
SCHEME_URL_FIELDS: tuple[str, ...] = ("image_url", "captcha_img", "captchaImg")

__all__ = ["SCHEME_URL_FIELDS", "URL_FIELDS", "validate_client_urls"]


def validate_client_urls(
    payload: Mapping[str, Any] | None,
    *,
    callback_url: str | None = None,
) -> None:
    """Raise `UrlRejectedError` for the first client URL that cannot be fetched.

    Validates every present `URL_FIELDS` key (top level; the wire shapes are
    flat) plus `callbackUrl`. A non-string value, an empty string or an absent
    key is not a URL and is left for the per-type schema to judge.
    """
    if callback_url is not None:
        _check(callback_url, "callbackUrl")
    if payload is None:
        return
    for field in URL_FIELDS:
        value = payload.get(field)
        if isinstance(value, str):
            _check(value, field)
    for field in SCHEME_URL_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
            _check(value, field)


def _check(value: str, field: str) -> None:
    text = value.strip()
    if not text:
        return
    candidate = _with_scheme(text)
    if any(char.isspace() for char in candidate):
        raise UrlRejectedError(
            "url contains whitespace",
            context={"field": field},
        )
    ensure_public_url_shape(candidate, field=field)


def _with_scheme(value: str) -> str:
    """Clients send `client-api.arkoselabs.com` where the wire field is a URL."""
    if "://" in value:
        return value
    if value.startswith("//"):
        return "https:" + value
    return "https://" + value
