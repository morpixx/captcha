"""Wire helpers shared by the two Akamai solvers.

Everything here is protocol plumbing: cookie jars, URL discovery, the two body
encodings. No solver policy -- what counts as solved lives in the solvers and in
abck.py, and no helper here turns a 200 into a success.

Payload mode is the primary input (docs/protocol.md, "Payload mode"): the client
sends the cookies and, when it already ran the page, the sensor script. A
bootstrap GET of websiteURL is opportunistic -- the transport raises
ChallengeExpiredError on 403/429/451 before a body is readable, which is exactly
the normal state of a protected page, so a failed bootstrap is a note, not a
task failure.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from captcha.domain import Task
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ConfigError, ProxyError, UnsolvableError

from .endpoints import SENSOR_FORM_KEY, SENSOR_FORM_TYPE, SENSOR_JSON_TYPE

MAX_URL_LEN = 2048
MAX_HTML_CHARS = 2_000_000
MAX_SCRIPT_CHARS = 8_000_000

_SRC_RE = re.compile(r"""<script[^>]+src=["']([^"']+)["']""", re.IGNORECASE)
_SBSD_RE = re.compile(r"""["']([^"']*?/sec/cp_challenge/[^"']*?sbsd[^"']*)["']""", re.IGNORECASE)
#: Path fragments that identify the tenant's sensor script.
_SCRIPT_MARKERS = ("/akam/", "akam/", "/_sec/", "bmak")

ACCEPT_HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
ACCEPT_ANY = "*/*"


@dataclass(frozen=True, slots=True)
class Bootstrap:
    """Outcome of the opportunistic page GET. Never a verdict."""

    url: str
    html: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    note: str | None = None


# ---- cookies ----------------------------------------------------------------


def cookies_of(raw: Any) -> dict[str, str]:
    """Normalize the task's cookies field: dict, "a=b; c=d", or [{name, value}]."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    if isinstance(raw, str):
        jar: dict[str, str] = {}
        for part in raw.split(";"):
            name, sep, value = part.partition("=")
            if sep and name.strip():
                jar[name.strip()] = value.strip()
        return jar
    if isinstance(raw, list | tuple):
        jar = {}
        for item in raw:
            if isinstance(item, dict) and item.get("name") and item.get("value") is not None:
                jar[str(item["name"])] = str(item["value"])
        return jar
    return {}


def merge_set_cookie(headers: list[str]) -> dict[str, str]:
    """Flatten repeated Set-Cookie headers; the last value wins (browser rule)."""
    jar: dict[str, str] = {}
    for raw in headers or ():
        if not raw:
            continue
        parsed = SimpleCookie()
        try:
            parsed.load(raw)
        except Exception:
            continue
        for name, morsel in parsed.items():
            jar[name] = morsel.value
    return jar


# ---- urls -------------------------------------------------------------------


def page_url(task: Task, payload: dict[str, Any]) -> str:
    """The protected page URL, validated as a short http(s) target."""
    for value in (
        task.site_url,
        payload.get("websiteURL"),
        payload.get("websiteUrl"),
        payload.get("pageurl"),
        payload.get("page_url"),
        payload.get("site_url"),
    ):
        if isinstance(value, str) and value.strip():
            return _clean_url(value.strip())
    raise UnsolvableError(
        "akamai: websiteURL is required (the session cookies and the script come from that page)",
        context={"type": task.type.value},
    )


def origin_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else "https://" + url)
    if not parsed.scheme or not parsed.netloc:
        raise UnsolvableError("akamai: cannot derive an origin from the URL", context={"url_len": len(url)})
    return f"{parsed.scheme}://{parsed.netloc}"


def _clean_url(url: str) -> str:
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if len(url) > MAX_URL_LEN or scheme not in ("http", "https"):
        raise UnsolvableError(
            "akamai: websiteURL must be a short http(s) URL",
            context={"scheme": scheme, "length": len(url)},
        )
    return url


def user_agent(task: Task, ctx: Any) -> str:
    """The UA the session runs under: task first, then the picked fingerprint.

    _abck is bound to the UA it was minted for. Falling back to a hardcoded one
    would produce a cookie the client cannot use, so a missing UA is a config
    error, not a default.
    """
    profile = _profile(ctx)
    payload = task.payload if isinstance(task.payload, dict) else {}
    for value in (task.user_agent, getattr(profile, "user_agent", None), payload.get("userAgent")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ConfigError(
        "akamai: no user agent on the task or in the fingerprint store; _abck is UA-bound",
        context={"type": task.type.value},
    )


def _profile(ctx: Any) -> Any:
    store = getattr(ctx, "fingerprint_store", None)
    if store is None:
        return None
    try:
        return store.pick()
    except CaptchaError:
        return None


# ---- discovery --------------------------------------------------------------


def sensor_script_url(html: str, base: str) -> str | None:
    """Find the tenant sensor script in the challenge HTML.

    Akamai serves it from a per-customer path (/akam/<ver>/<hash>, /_sec/..., or
    an inline loader that names bmak); the marker list is intentionally loose
    and the URL is re-based on the page, never on a host from the HTML.
    """
    for src in _script_sources(html):
        lowered = src.lower()
        if any(marker in lowered for marker in _SCRIPT_MARKERS):
            return _same_host(urljoin(base, src), base)
    return None


def sbsd_url(html: str, base: str) -> str | None:
    """Find the SBSD challenge path the page points at, if it names one."""
    match = _SBSD_RE.search(html or "")
    if not match:
        return None
    return _same_host(urljoin(base, match.group(1)), base)


def _script_sources(html: str) -> list[str]:
    return [m.group(1).strip() for m in _SRC_RE.finditer((html or "")[:MAX_HTML_CHARS]) if m.group(1).strip()]


def _same_host(url: str, base: str) -> str | None:
    """Only accept a URL on the page's own host: a cross-host "script" is a redirect."""
    try:
        host = (urlparse(url).hostname or "").lower()
        base_host = (urlparse(base).hostname or "").lower()
    except ValueError:
        return None
    if not host or not base_host or host != base_host:
        return None
    return url


# ---- bodies -----------------------------------------------------------------


def sensor_body(sensor_data: str, *, form: bool) -> tuple[Any, dict[str, str]]:
    """Wrap sensor_data in the POST body the tenant expects.

    v3 tenants take JSON; 2.x tenants take a urlencoded form with one field. The
    selector is the task's abck_version / sensor_format, never a guess from the
    response, because a wrong encoding is indistinguishable from a bad sensor.
    """
    if form:
        return f"{SENSOR_FORM_KEY}={quote(sensor_data, safe='')}".encode(), {"Content-Type": SENSOR_FORM_TYPE}
    return {"sensor_data": sensor_data}, {"Content-Type": SENSOR_JSON_TYPE}


def wants_form(payload: dict[str, Any]) -> bool:
    fmt = str(payload.get("sensor_format") or "").strip().lower()
    if fmt:
        return fmt == "form"
    version = payload.get("abck_version")
    try:
        return version is not None and int(version) < 3
    except (TypeError, ValueError):
        return False


# ---- i/o --------------------------------------------------------------------


async def bootstrap(ctx: Any, url: str, jar: dict[str, str], ua: str) -> Bootstrap:
    """One opportunistic page GET: cookies and HTML when the edge allows it."""
    try:
        resp = await ctx.http.get(
            url,
            headers={"User-Agent": ua, "Accept": ACCEPT_HTML, "Accept-Language": "en-US,en;q=0.9"},
            cookies=jar or None,
        )
    except ProxyError:
        raise
    except CaptchaError as exc:
        return Bootstrap(url=url, note=exc.code)
    html = resp.text[:MAX_HTML_CHARS]
    return Bootstrap(url=url, html=html, cookies=merge_set_cookie(resp.headers_all("set-cookie")))


async def fetch_text(ctx: Any, url: str, jar: dict[str, str], ua: str, *, referer: str) -> str:
    """GET a challenge script and return its source. Never a partial payload."""
    try:
        resp = await ctx.http.get(
            url,
            headers={"User-Agent": ua, "Accept": ACCEPT_ANY, "Referer": referer},
            cookies=jar or None,
        )
    except ProxyError:
        raise
    except CaptchaError as exc:
        raise ChallengeExpiredError(
            "akamai: challenge script fetch was rejected",
            context={"reason": exc.code, "url_len": len(url)},
        ) from exc
    text = resp.text
    if resp.status >= 400 or not text.strip():
        raise ChallengeExpiredError(
            "akamai: challenge script came back empty",
            context={"status": resp.status, "chars": len(text)},
        )
    return text[:MAX_SCRIPT_CHARS]


# ---- misc -------------------------------------------------------------------


def first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def now_ms() -> int:
    return int(time.time() * 1000)
