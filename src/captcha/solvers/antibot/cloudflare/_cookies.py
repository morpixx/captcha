"""Cookie capture for the Cloudflare browser-lane solvers.

`cf_clearance` is HttpOnly, so `document.cookie` cannot see it: the jar has to
come from the browser context. A context also holds cookies for
`challenges.cloudflare.com` (the widget), and handing those to the client looks
like a solution while being useless -- hence the host filter.
"""

from __future__ import annotations

from typing import Any

from captcha.domain import Task

from ._common import maybe_await

CLEARANCE_COOKIE = "cf_clearance"


async def cookie_jar(session: Any, *, host: str) -> dict[str, str]:
    """Cookies the browser holds for `host`, as a flat name -> value map."""
    context = getattr(session, "context", None)
    getter = getattr(context, "cookies", None)
    if getter is None:
        return {}
    try:
        raw = await maybe_await(getter() if callable(getter) else getter)
    except Exception:
        return {}
    return cookie_map(raw, host=host)


def cookie_map(raw: Any, *, host: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw or ():
        if not isinstance(item, dict):
            continue
        name, value = item.get("name"), item.get("value")
        if not name or value is None:
            continue
        domain = item.get("domain")
        if host and isinstance(domain, str) and domain and not domain_matches(host, domain):
            continue
        out[str(name)] = str(value)
    return out


def domain_matches(host: str, domain: str) -> bool:
    host, domain = host.lower().strip("."), domain.lower().lstrip(".").strip(".")
    if not host or not domain:
        return False
    return host == domain or host.endswith("." + domain)


def capture_ua(out: dict[str, Any], task: Task) -> str | None:
    """The UA that minted the answer; the client has to submit with it.

    Falls back to the client-supplied UA (Turnstile tokens are not UA-bound).
    """
    ua = page_ua(out)
    if ua:
        return ua
    if isinstance(task.user_agent, str) and task.user_agent.strip():
        return task.user_agent.strip()
    return None


def page_ua(out: dict[str, Any]) -> str | None:
    """Only the UA the page reported. cf_clearance is UA-bound: a client-supplied
    UA would name a browser that never held the cookie."""
    for key in ("ua", "userAgent", "user_agent"):
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
