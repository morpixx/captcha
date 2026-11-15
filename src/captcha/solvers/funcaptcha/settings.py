"""Arkose enforcement settings, read off the served api.js.

Everything the wire needs that is not client-supplied comes from one GET:

    {surl}/v2/{public_key}/api.js

  * capi_version  — sent as a form field, must match the bundle the site loads;
  * build_id      — the enforcement build UUID, sent as ark-build-id and used
                    to decide whether the BDA field is named c or bda;
  * enforcement hash — goes into the BDA as 6a62b2a558;
  * RSA public key — the site's SPKI, wrapped around the BDA content key.

The RSA key is not a literal in api.js: in current builds it lives XOR-encoded
inside the stack-VM program. Static extraction from that program is a separate
research artefact (see docs/reverse-2026.md), so this module does what is
honest: use the key when it is plainly present in the bundle or pinned by the
caller (payload['rsa_public_key'] / ctx.extra['arkose_rsa_key']), and raise
SensorError with the pin hint when it is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from captcha.domain import Task
from captcha.domain.errors import ConfigError, SensorError
from captcha.solvers import SolverContext

DEFAULT_SURL = "https://client-api.arkoselabs.com"
API_JS_PATH = "/v2/{pk}/api.js"

_CAPI_PATTERNS = (
    r'f="v2/api\.js",l="([\d.]+)"',
    r',l="(\d+\.\d+\.\d+)",p=',
    r'"(\d+\.\d+\.\d+)/enforcement\.',
)
_BUILD_ID_RE = re.compile(r'"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"')
_ENFORCEMENT_RE = re.compile(r"(\d+\.\d+\.\d+)/enforcement\.([0-9a-f]{32})\.html")
_SPKI_RE = re.compile(r"MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8A[A-Za-z0-9+/]{300,}={0,3}")
_BODY_WRAPPER = "body"

#: Payload keys clients use for the verify host, in order of preference. 2captcha
#: calls it surl, capsolver calls it funcaptchaApiJSSubdomain, our legacy form
#: names it surl too but some integrations send api_server.
SURL_KEYS = (
    "surl",
    "funcaptchaApiJSSubdomain",
    "api_js_subdomain",
    "arkose_host",
    "api_server",
    "host",
)
PUBLIC_KEY_KEYS = ("publickey", "public_key", "websitePublicKey", "sitekey")


@dataclass(frozen=True, slots=True)
class ArkoseSettings:
    surl: str
    public_key: str
    capi_version: str = "4.4.5"
    build_id: str = ""
    enforcement_hash: str = ""
    rsa_public_key: str = ""

    @property
    def bda_field(self) -> str:
        """Current builds name the encrypted BDA c; older ones name it bda."""
        return "c" if self.build_id else "bda"


def public_key_for(task: Task) -> str:
    payload = task.payload or {}
    if isinstance(task.sitekey, str) and task.sitekey.strip():
        return task.sitekey.strip()
    for key in PUBLIC_KEY_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise SensorError(
        "funcaptcha payload has no public key",
        context={"keys": sorted(payload), "hint": "send websitePublicKey / publickey / sitekey"},
    )


def surl_for(task: Task) -> str:
    payload = task.payload or {}
    for key in SURL_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_surl(value)
    return DEFAULT_SURL


def normalize_surl(value: str) -> str:
    """Accept bare host, host/path or full URL; always return scheme://host[:port]."""
    raw = value.strip().rstrip("/")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.hostname:
        raise SensorError("funcaptcha surl is not a host", context={"surl": value[:120]})
    if parsed.scheme not in ("http", "https"):
        raise SensorError("funcaptcha surl must be http(s)", context={"scheme": parsed.scheme})
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


async def fetch_settings(
    ctx: SolverContext,
    *,
    surl: str,
    public_key: str,
    user_agent: str = "",
    pinned_rsa: str = "",
) -> ArkoseSettings:
    """GET api.js once and parse the four constants the wire depends on."""
    if ctx.http is None:
        raise ConfigError("funcaptcha needs ctx.http", context={"surl": surl})
    url = surl + API_JS_PATH.format(pk=public_key)
    headers = {"accept": "application/javascript,*/*;q=0.8"}
    if user_agent:
        headers["user-agent"] = user_agent
    response = await ctx.http.get(url, headers=headers)
    if response.status != 200 or not response.body:
        raise SensorError(
            "arkose api.js fetch failed",
            context={"status": response.status, "surl": surl, "bytes": len(response.body or b"")},
        )

    loader = _unwrap(response.text)
    capi = _first_match(_CAPI_PATTERNS, loader) or "4.4.5"
    build = _match(_BUILD_ID_RE, loader) or ""
    enforcement = _match(_ENFORCEMENT_RE, loader, group=2) or ""
    rsa = pinned_rsa.strip() or extract_rsa(loader)
    return ArkoseSettings(
        surl=surl,
        public_key=public_key,
        capi_version=capi,
        build_id=build,
        enforcement_hash=enforcement,
        rsa_public_key=rsa,
    )


def require_rsa(settings: ArkoseSettings) -> str:
    """Fail with the pin hint instead of shipping a BDA nobody can decrypt."""
    if settings.rsa_public_key:
        return settings.rsa_public_key
    raise SensorError(
        "arkose api.js carries no plaintext RSA key (VM-encoded build)",
        context={
            "surl": settings.surl,
            "capi_version": settings.capi_version,
            "hint": "pin it: payload['rsa_public_key'] or ctx.extra['arkose_rsa_key']",
            "next": "docs/funcaptcha-bda.md — static extraction from the VM program",
        },
    )


def extract_rsa(loader: str) -> str:
    """Pull an SPKI blob out of the bundle when the build still keeps it literal."""
    match = _SPKI_RE.search(loader or "")
    if not match:
        return ""
    blob = match.group(0)
    # trailing base64 padding is not part of the regex body; re-add to a multiple of 4
    return blob + "=" * (-len(blob) % 4)


def _unwrap(text: str) -> str:
    """Some builds wrap the script in {"body": "<js>"}."""
    import json

    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return text
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict) and isinstance(parsed.get(_BODY_WRAPPER), str):
        return parsed[_BODY_WRAPPER]
    return text


def _match(pattern: re.Pattern[str], text: str, *, group: int = 1) -> str:
    found = pattern.search(text or "")
    return found.group(group) if found else ""


def _first_match(patterns: tuple[str, ...], text: str) -> str:
    for pattern in patterns:
        found = re.search(pattern, text or "")
        if found:
            return found.group(1)
    return ""
