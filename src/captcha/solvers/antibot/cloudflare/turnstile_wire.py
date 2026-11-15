"""Turnstile wire facts: payload parsing and the public HTTP/JS bootstrap.

What is public (docs/reverse-2026.md §3, capture of 2026-08):

  GET  /turnstile/v0/api.js              -> 302 to a versioned bundle
  GET  /turnstile/v0/b/<hash>/api.js     -> widget bundle
  POST /cdn-cgi/challenge-platform/h/<v>/turnstile/f/av0/rch/<seg>/<sitekey>
                                         -> cf-turnstile-response token

The mint POST's byte schema is gated: no public implementation reproduces it and
the proof is server-issued. So the HTTP path resolves the unversioned loader,
reads the challenge-platform variant the bundle was built for, and probes the
widget document -- then the solver escalates to the browser lane instead of
inventing the missing body. Probe failures are recorded, not fatal: a browser
can still solve from the page, so they only land in the escalation context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import CaptchaError, ProxyError, UnsolvableError

from .endpoints import (
    DEFAULT_VARIANT,
    TURNSTILE_API_JS,
    TURNSTILE_BUNDLE_RE,
    TURNSTILE_HOST,
    TURNSTILE_ORIGIN,
    TURNSTILE_WIDGET_FMT,
)

HTTP_TIMEOUT = 20.0
MAX_BODY_CHARS = 2_000_000
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_VARIANT_RE = re.compile(r"/cdn-cgi/challenge-platform/h/([a-z])/turnstile/")
_BUNDLE_RE = re.compile(TURNSTILE_BUNDLE_RE)
_MAX_ACTION = 32
_MAX_CDATA = 255


@dataclass(frozen=True, slots=True)
class TurnstileParams:
    """Everything the widget is configured with, normalized from the task."""

    sitekey: str
    site_url: str
    action: str | None = None
    cdata: str | None = None
    enterprise: bool = False
    invisible: bool = False
    user_agent: str | None = None


@dataclass(frozen=True, slots=True)
class Bootstrap:
    """Outcome of the browser-free part of the flow. Never a token."""

    bundle_url: str | None
    variant: str
    widget_url: str
    widget_status: int | None
    error: str | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, Any]:
        return {
            "bundle": self.bundle_url,
            "variant": self.variant,
            "widget_status": self.widget_status,
            "bootstrap_error": self.error,
            "bootstrap_notes": list(self.notes),
        }


def params_from_task(task: Task) -> TurnstileParams:
    """Normalize task + payload into widget parameters, or fail structurally."""
    payload = task.payload if isinstance(task.payload, dict) else {}
    sitekey = _first_str(task.sitekey, payload.get("sitekey"), payload.get("siteKey"), payload.get("websiteKey"))
    if not sitekey:
        raise UnsolvableError("turnstile: sitekey is required", context={"task": str(task.id)})
    if len(sitekey) > 256 or not sitekey.isprintable() or any(ch.isspace() for ch in sitekey):
        raise UnsolvableError("turnstile: malformed sitekey", context={"task": str(task.id)})

    site_url = _first_str(
        task.site_url,
        payload.get("websiteURL"),
        payload.get("websiteUrl"),
        payload.get("pageurl"),
        payload.get("page_url"),
        payload.get("site_url"),
    )
    if not site_url:
        raise UnsolvableError(
            "turnstile: websiteURL is required (the token is bound to the hostname)",
            context={"task": str(task.id), "sitekey": sitekey},
        )

    invisible = task.type is CaptchaType.TURNSTILE_INVISIBLE or truthy(
        payload.get("invisible", payload.get("isInvisible"))
    )
    return TurnstileParams(
        sitekey=sitekey,
        site_url=site_url,
        action=_clean_action(payload.get("action")),
        cdata=_clean_cdata(_first_str(payload.get("cdata"), payload.get("cData"), payload.get("chlPageData"))),
        enterprise=truthy(payload.get("enterprise")),
        invisible=invisible,
        user_agent=_first_str(task.user_agent, payload.get("userAgent"), payload.get("user_agent")),
    )


def widget_url(sitekey: str, variant: str) -> str:
    return TURNSTILE_WIDGET_FMT.format(variant=variant, sitekey=quote(sitekey, safe=""))


async def bootstrap(http: Any, params: TurnstileParams) -> Bootstrap:
    """Run the browser-free part of the Turnstile flow and report what it saw."""
    notes: list[str] = []

    loader = await _get(
        http,
        TURNSTILE_API_JS,
        params,
        query={"render": "explicit", "onload": "cfTurnstileOnload"},
        diag=notes,
    )
    bundle_url: str | None = None
    if loader is None:
        pass
    elif loader.status >= 400:
        notes.append(f"api.js HTTP {loader.status}")
    else:
        bundle_url = discover_bundle(loader)

    variant = DEFAULT_VARIANT
    if bundle_url:
        bundle = await _get(http, bundle_url, params, diag=notes)
        if bundle is None:
            pass
        elif 200 <= bundle.status < 300:
            variant = parse_variant(bundle.text[:MAX_BODY_CHARS]) or DEFAULT_VARIANT
        else:
            notes.append(f"bundle HTTP {bundle.status}")

    url = widget_url(params.sitekey, variant)
    widget = await _get(
        http,
        url,
        params,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Sec-Fetch-Dest": "iframe",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
        },
        diag=notes,
    )
    status = None if widget is None else widget.status
    if widget is not None and widget.status >= 400:
        notes.append(f"widget HTTP {widget.status}")

    return Bootstrap(
        bundle_url=bundle_url,
        variant=variant,
        widget_url=url,
        widget_status=status,
        error=notes[0] if notes else None,
        notes=tuple(notes),
    )


def discover_bundle(resp: Any) -> str | None:
    """Find the versioned bundle URL: redirect target, Location, or loader body.

    The body is only trusted for a *relative* path. An absolute URL there names
    some other host, and silently rebasing it onto challenges.cloudflare.com
    would turn a compromised loader into a bundle we never meant to read.
    """
    for candidate in (getattr(resp, "final_url", None), _header(resp, "location")):
        url = _safe_bundle_url(candidate)
        if url:
            return url
    text = getattr(resp, "text", "") or ""
    match = _BUNDLE_RE.search(text)
    while match:
        before = text[match.start() - 1] if match.start() else ""
        if not before or not (before.isalnum() or before in ".-_"):
            return _safe_bundle_url(match.group(0))
        match = _BUNDLE_RE.search(text, match.end())
    return None


def parse_variant(text: str) -> str | None:
    """The challenge-platform build segment the widget was compiled against."""
    match = _VARIANT_RE.search(text or "")
    return match.group(1) if match else None


def truthy(value: Any) -> bool:
    """2captcha clients send booleans as "true"/"false" strings as often as bools."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


async def _get(
    http: Any,
    url: str,
    params: TurnstileParams,
    *,
    headers: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    diag: list[str],
) -> Any:
    """One bootstrap GET. Network/antibot failures are recorded and swallowed."""
    request_headers = {
        "User-Agent": params.user_agent or DEFAULT_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": params.site_url,
    }
    request_headers.update(headers or {})
    try:
        return await http.get(url, headers=request_headers, params=query, timeout=HTTP_TIMEOUT)
    except ProxyError:
        raise
    except CaptchaError as exc:
        # The transport turns 403/429/451 into ChallengeExpiredError. For a
        # probe that just means "no browser-free path"; the browser can still
        # load the real page, so it must not fail the HTTP lane.
        diag.append(f"{_short(url)}: {exc.code}")
        return None
    except OSError as exc:
        # Connection reset / timeout. Anything else is our bug and should raise.
        diag.append(f"{_short(url)}: {type(exc).__name__}")
        return None


def _safe_bundle_url(value: Any) -> str | None:
    """Resolve a bundle path, but only on challenges.cloudflare.com over https."""
    if not isinstance(value, str) or not value:
        return None
    url = urljoin(TURNSTILE_ORIGIN + "/", value.strip())
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != TURNSTILE_HOST:
        return None
    return url if _BUNDLE_RE.search(parsed.path) else None


def _header(resp: Any, name: str) -> str | None:
    getter = getattr(resp, "header", None)
    if not callable(getter):
        return None
    try:
        value = getter(name)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def _clean_action(value: Any) -> str | None:
    action = _first_str(value)
    if action is None:
        return None
    if len(action) > _MAX_ACTION or not re.fullmatch(r"[A-Za-z0-9_-]+", action):
        raise UnsolvableError(
            "turnstile: invalid action (alphanumeric, _ and -, <= 32)", context={"action_len": len(action)}
        )
    return action


def _clean_cdata(value: Any) -> str | None:
    cdata = _first_str(value)
    if cdata is None:
        return None
    if len(cdata) > _MAX_CDATA:
        raise UnsolvableError("turnstile: cData is limited to 255 characters", context={"cdata_len": len(cdata)})
    return cdata


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def _short(url: str) -> str:
    return url.split("://", 1)[-1].split("?", 1)[0][:80]
