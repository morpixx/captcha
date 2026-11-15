"""Shared reCAPTCHA wire helpers used by the v2 and v3 solvers.

Both families walk the same anchor -> reload conversation:

    api.js   -> current release slug (the `v` parameter)
    anchor   -> hidden `recaptcha-token` (the `c` parameter)
    reload   -> challenge token, and for v3 the final `rresp` token
    payload  -> audio MP3 (v2 audio path)
    userverify -> final token (v2 audio path)

Keeping the endpoints, the origin encoder and the response parsers in one place
means a Google schema change touches one file. The parsers are deliberately
tolerant: the reload body has been a plain JS array, an XSSI-guarded JSON
array, and (for some builds) a protobuf response whose `rresp` string is all
we need. None of these helpers do I/O.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse

from captcha.domain import Task
from captcha.domain.errors import ChallengeExpiredError, ConfigError
from captcha.solvers.base import SolverContext

GOOGLE_HOST = "https://www.google.com"

#: Fallback release slug used for the first anchor attempt. It is refreshed
#: from api.js / enterprise.js when the anchor rejects it.
DEFAULT_VERSION = "DKw2Q4K7L4-p0e50T4ieYFkC"
#: Historical name kept for callers that pinned the slug directly.
API_VERSION = DEFAULT_VERSION

_API2 = "/recaptcha/api2"
_ENTERPRISE = "/recaptcha/enterprise"


@dataclass(frozen=True)
class Endpoints:
    """The four Google endpoints plus the JS bootstrap for one family."""

    anchor: str
    reload: str
    payload: str
    userverify: str
    js: str


API2 = Endpoints(
    anchor=GOOGLE_HOST + _API2 + "/anchor",
    reload=GOOGLE_HOST + _API2 + "/reload",
    payload=GOOGLE_HOST + _API2 + "/payload",
    userverify=GOOGLE_HOST + _API2 + "/userverify",
    js=GOOGLE_HOST + "/recaptcha/api.js",
)

ENTERPRISE = Endpoints(
    anchor=GOOGLE_HOST + _ENTERPRISE + "/anchor",
    reload=GOOGLE_HOST + _ENTERPRISE + "/reload",
    payload=GOOGLE_HOST + _ENTERPRISE + "/payload",
    userverify=GOOGLE_HOST + _ENTERPRISE + "/userverify",
    js=GOOGLE_HOST + "/recaptcha/enterprise.js",
)

_ANCHOR_TOKEN_RE = re.compile(r'id="recaptcha-token"[^>]*?value="([^"]+)"')
_ANCHOR_TOKEN_REVERSED_RE = re.compile(r'value="([^"]+)"[^>]*?id="recaptcha-token"')
_RELEASE_RE = re.compile(r"recaptcha/releases/([A-Za-z0-9_-]+)")
_RRESP_RE = re.compile(r'"rresp"\s*,\s*"([^"]+)"')
_PAYLOAD_URL_RE = re.compile(r"https://www\.google\.com/recaptcha/(?:api2|enterprise)/payload[^\"'\s\\]+")


def endpoints_for(enterprise: bool) -> Endpoints:
    return ENTERPRISE if enterprise else API2


def encode_origin(site_url: str) -> str:
    """Google's `co`: base64 of `scheme://host:port`, padded with `.`.

    A literal `=` (or `%3D`) makes the anchor endpoint render its
    unsupported-browser page without a `recaptcha-token`.
    """
    parsed = urlparse(site_url if "//" in site_url else "https://" + site_url)
    scheme = (parsed.scheme or "https").lower()
    host = parsed.hostname
    if not host:
        raise ConfigError(
            "reCAPTCHA site_url has no host",
            context={"site_url": site_url},
        )
    port = parsed.port or (443 if scheme == "https" else 80)
    blob = base64.b64encode(f"{scheme}://{host}:{port}".encode()).decode("ascii")
    return blob.replace("=", ".")


def random_cb() -> str:
    """`cb` is a 12-char lowercase+digits token minted by the client."""
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def strip_xssi(body: str) -> str:
    """Remove Google's anti-XSSI prefix from a reload/userverify body."""
    text = (body or "").lstrip()
    if text.startswith(")]}'"):
        text = text[4:]
    return text.lstrip()


def initial_version(task: Task) -> str:
    """Version for the first anchor attempt: payload override or fallback."""
    payload = task.payload or {}
    explicit = payload.get("v") or payload.get("version")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return DEFAULT_VERSION


async def resolve_version(task: Task, ctx: SolverContext, endpoints: Endpoints) -> str:
    """Fetch the current grecaptcha release slug from api.js / enterprise.js.

    Called only after an anchor attempt fails: the first attempt uses
    `payload["v"]` or `DEFAULT_VERSION`, so the happy path avoids an extra
    request. A missing slug means the bootstrap page changed shape.
    """
    payload = task.payload or {}
    explicit = payload.get("v") or payload.get("version")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    if ctx.http is None:
        raise ConfigError(
            "reCAPTCHA requires ctx.http to resolve the script version",
            context={"task": str(task.id)},
        )
    resp = await ctx.http.get(endpoints.js, params={"render": task.sitekey})
    match = _RELEASE_RE.search(resp.text or "")
    if not match:
        raise ChallengeExpiredError(
            "reCAPTCHA bootstrap returned no release slug — pin payload['v']",
            context={"status": resp.status, "url": resp.final_url},
        )
    return match.group(1)


def extract_anchor_token(html: str) -> str | None:
    """Pull the hidden `recaptcha-token` value out of anchor HTML."""
    for pattern in (_ANCHOR_TOKEN_RE, _ANCHOR_TOKEN_REVERSED_RE):
        match = pattern.search(html or "")
        if match:
            return match.group(1)
    return None


def extract_rresp(body: str) -> str | None:
    """Extract the `rresp` token from a reload/userverify response.

    Handles the canonical XSSI-guarded `["rresp","<token>",...]` body,
    a nested array, a dict with an `rresp` key, malformed JSON, and a
    protobuf-ish body where the field name and token are raw bytes. Returns
    None when no token is present.
    """
    text = strip_xssi(body)
    if not text:
        return None

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if parsed is not None:
        token = _walk_rresp(parsed)
        if token:
            return token

    match = _RRESP_RE.search(text)
    if match:
        return match.group(1)
    return _rresp_from_raw(text)


def _rresp_from_raw(text: str) -> str | None:
    """Last-resort scan for a protobuf-ish body carrying `rresp` + a token.

    A protobuf response has no quotes around the pair, but the field name and
    the ASCII token are still contiguous after a length prefix. Only called
    when the JSON and quoted-pair parsers found nothing.
    """
    marker = text.find("rresp")
    if marker == -1:
        return None
    match = re.search(r"[A-Za-z0-9_-]{16,}", text[marker + 5 :])
    return match.group(0) if match else None


def _walk_rresp(node: object) -> str | None:
    if isinstance(node, list):
        if len(node) >= 2 and node[0] == "rresp" and isinstance(node[1], str) and node[1]:
            return node[1]
        for item in node:
            found = _walk_rresp(item)
            if found:
                return found
    elif isinstance(node, dict):
        value = node.get("rresp")
        if isinstance(value, str) and value:
            return value
        for item in node.values():
            found = _walk_rresp(item)
            if found:
                return found
    return None


def extract_reload_token(body: str, index: int = 1) -> str | None:
    """Tolerant reload parser: prefer `rresp`, fall back to array index.

    Older v2 audio captures answer with a bare quoted array, so `rresp` alone
    is not enough there.
    """
    token = extract_rresp(body)
    if token:
        return token
    text = strip_xssi(body)
    for position in (index, 0):
        candidate = _quoted_field(text, position)
        if candidate and candidate != "rresp":
            return candidate
    return None


def _quoted_field(text: str, index: int) -> str | None:
    if not text.startswith("["):
        return None
    i = 0
    count = 0
    length = len(text)
    while i < length:
        if text[i] != '"':
            i += 1
            continue
        j = i + 1
        buf: list[str] = []
        while j < length and text[j] != '"':
            if text[j] == "\\" and j + 1 < length:
                buf.append(text[j + 1])
                j += 2
                continue
            buf.append(text[j])
            j += 1
        if j >= length:
            return None
        if count == index:
            return "".join(buf)
        count += 1
        i = j + 1
    return None


def extract_audio_url(body: str) -> str | None:
    """Find an audio payload URL embedded in a reload response, if any."""
    text = (body or "").replace("\\/", "/")
    match = _PAYLOAD_URL_RE.search(text)
    return match.group(0) if match else None


def audio_payload_url(endpoints: Endpoints, token: str, *, sitekey: str, param: str = "c") -> str:
    """Build the legacy audio endpoint URL.

    Modern reload responses usually carry a full `payload?p=...` URL; this is
    the fallback for captures that do not. `param="p"` is the second attempt
    because Google has flipped between `c` and `p` across builds.
    """
    query = urlencode({"p": token}) if param == "p" else urlencode({"c": token, "k": sitekey, "id": "2"})
    return f"{endpoints.payload}?{query}"
