"""Kasada wire helpers: cookies, origin, script discovery.

Payload mode is the primary input (docs/protocol.md): the client sends the
cookies and, when it already ran the 429 page, the SDK script itself. A
bootstrap GET of websiteURL is opportunistic -- the transport turns 403/429/451
into ChallengeExpiredError before a body is readable, so a 429 page never yields
its ips.js path from here. That is why a missing script is a task-level error
with an actionable message rather than a silent retry.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urljoin, urlparse

from captcha.domain import Task
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, ConfigError, ProxyError, UnsolvableError

from .endpoints import KPSDK_UUID_IPS, VERSION_RE

MAX_URL_LEN = 2048
MAX_HTML_CHARS = 2_000_000
MAX_SCRIPT_CHARS = 8_000_000

#: Quoted strings in HTML/JS. Filtered in Python: the SDK path is stable but the
#: query string is not, so a pattern on the whole URL would rot.
_QUOTED_RE = re.compile(r"""["']([^"'\s]{8,2048})["']""")
_VERSION_RE = re.compile(VERSION_RE)

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
    for value in (
        task.site_url,
        payload.get("websiteURL"),
        payload.get("websiteUrl"),
        payload.get("pageurl"),
        payload.get("page_url"),
        payload.get("site_url"),
        payload.get("origin"),
    ):
        if isinstance(value, str) and value.strip():
            return _clean_url(value.strip())
    raise UnsolvableError(
        "kasada: websiteURL (or origin) is required",
        context={"type": task.type.value},
    )


def origin_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else "https://" + url)
    if not parsed.scheme or not parsed.netloc:
        raise UnsolvableError("kasada: cannot derive an origin from the URL", context={"url_len": len(url)})
    return f"{parsed.scheme}://{parsed.netloc}"


def same_host(url: str, base: str) -> str | None:
    """Only accept a URL on the page's own host: a cross-host script is a redirect."""
    try:
        host = (urlparse(url).hostname or "").lower()
        base_host = (urlparse(base).hostname or "").lower()
    except ValueError:
        return None
    if not host or not base_host or host != base_host:
        return None
    return url


def _clean_url(url: str) -> str:
    scheme = url.split("://", 1)[0].lower() if "://" in url else ""
    if len(url) > MAX_URL_LEN or scheme not in ("http", "https"):
        raise UnsolvableError(
            "kasada: websiteURL must be a short http(s) URL",
            context={"scheme": scheme, "length": len(url)},
        )
    return url


def user_agent(task: Task, ctx: Any) -> str:
    """ct is bound to the UA it was minted for; a default would be unusable."""
    profile = _profile(ctx)
    payload = task.payload if isinstance(task.payload, dict) else {}
    for value in (task.user_agent, getattr(profile, "user_agent", None), payload.get("userAgent")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ConfigError(
        "kasada: no user agent on the task or in the fingerprint store; x-kpsdk-ct is UA-bound",
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


# ---- script discovery -------------------------------------------------------


def script_path(html: str) -> str | None:
    """Find the SDK script path in a challenge page or inline framework blob."""
    for candidate in _QUOTED_RE.findall((html or "")[:MAX_HTML_CHARS]):
        lowered = candidate.lower()
        if KPSDK_UUID_IPS in lowered and ".js" in lowered:
            return candidate
    return None


def script_url(html: str, base: str) -> str | None:
    path = script_path(html)
    if path is None:
        return None
    return same_host(urljoin(base, path), base)


def version_hint(script: str) -> str | None:
    """Best-effort x-kpsdk-v from the script source.

    The live value is decoded inside p.js at run time, so a regex hit is a hint
    that saves one request, not a source of truth: the solver omits the header
    when this returns None instead of sending a made-up version.
    """
    match = _VERSION_RE.search(script or "")
    return match.group(0) if match else None


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
    return Bootstrap(url=url, html=resp.text[:MAX_HTML_CHARS], cookies=merge_set_cookie(resp.headers_all("set-cookie")))


async def fetch_text(ctx: Any, url: str, jar: dict[str, str], ua: str, *, referer: str) -> str:
    """GET the SDK script. An empty or rejected fetch is never a payload source."""
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
            "kasada: SDK script fetch was rejected",
            context={"reason": exc.code, "url_len": len(url)},
        ) from exc
    text = resp.text
    if resp.status >= 400 or not text.strip():
        raise ChallengeExpiredError(
            "kasada: SDK script came back empty",
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


async def mfc(
    ctx: Any,
    url: str,
    jar: dict[str, str],
    ua: str,
    *,
    referer: str,
    ct: str,
    version: str | None,
) -> tuple[str | None, str | None, str | None]:
    """GET /mfc for fc (a cd input) and h. A failure is a note, not a task failure.

    Not every tenant mounts /mfc, and the ct answer is complete without fc, so a
    4xx or a transport verdict degrades the answer instead of failing the task.
    """
    headers = {"User-Agent": ua, "Accept": ACCEPT_ANY, "Referer": referer, "x-kpsdk-ct": ct}
    if version:
        headers["x-kpsdk-v"] = version
    try:
        resp = await ctx.http.get(url, headers=headers, cookies=jar or None)
    except ProxyError:
        raise
    except CaptchaError as exc:
        return None, None, exc.code
    jar.update(merge_set_cookie(resp.headers_all("set-cookie")))
    if resp.status >= 400:
        return None, None, f"status:{resp.status}"
    return resp.header("x-kpsdk-fc"), resp.header("x-kpsdk-h"), None
