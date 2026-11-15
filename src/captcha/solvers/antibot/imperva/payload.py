"""Payload coercion for the Imperva solver.

The 2captcha payload for this family is loose: the protected page arrives as
`websiteURL`/`pageurl`/`url`, sometimes only as `origin`; the reese
script as `reeseScriptURL` (Capsolver's name), `challenge_js` or `script`.
Normalizing once keeps the solver about the two paths it has instead of a pile
of `dict.get` fallbacks.
"""

from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain import Task
from captcha.domain.errors import UnsolvableError
from captcha.solvers.antibot._runtime import cookies_to_dict

DEFAULT_JS_TIMEOUT_MS = 15_000
DEFAULT_COOKIE_TIMEOUT_S = 20.0
MAX_URL_LEN = 2048


def origin(task: Task, payload: dict[str, Any]) -> str:
    """Site root the sensor POST goes back to; must be http(s)."""
    rejected: str | None = None
    for value in (
        payload.get("origin"),
        payload.get("websiteURL"),
        payload.get("websiteUrl"),
        payload.get("pageurl"),
        task.site_url,
    ):
        if not isinstance(value, str) or not value.strip():
            continue
        url = value.strip()
        if len(url) <= MAX_URL_LEN and url.lower().startswith(("http://", "https://")):
            parsed = urlparse(url)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
            return url.rstrip("/")
        rejected = rejected or url
    if rejected is not None:
        raise UnsolvableError(
            "imperva origin must be a short http(s) URL",
            context={"length": len(rejected)},
        )
    raise UnsolvableError(
        "imperva needs the protected origin (websiteURL or origin)",
        context={"type": task.type.value},
    )


def user_agent(task: Task, payload: dict[str, Any]) -> str | None:
    value = task.user_agent or payload.get("userAgent") or payload.get("user_agent") or payload.get("ua")
    return str(value) if value else None


def payload_cookies(payload: dict[str, Any]) -> dict[str, str]:
    """Cookies of a previous round: reese84, incap_ses, ___utmvc, nlbi."""
    jar = cookies_to_dict(payload.get("cookies"))
    for name in ("reese84", "___utmvc"):
        value = payload.get(name)
        if isinstance(value, str) and value:
            jar.setdefault(name, value)
    return jar


def challenge_js(payload: dict[str, Any]) -> str:
    """The reese / main.js source, under any of the names clients use."""
    for key in ("challenge_js", "reese_script", "reeseScript", "script", "main_js"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def reese_script_url(task: Task, payload: dict[str, Any]) -> str:
    """URL of the dynamic reese script, when the client sent one instead of source."""
    for key in ("reeseScriptURL", "reese_script_url", "script_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
            return value.strip()
    return ""


def nonce(payload: dict[str, Any]) -> str:
    """Per-session `e` query value; the client may pin one for a replayed run."""
    value = payload.get("nonce")
    if isinstance(value, str) and value:
        return value
    return secrets.token_hex(4)


def version(payload: dict[str, Any]) -> str:
    value = payload.get("version")
    return str(value) if value else ""


def domain(task: Task, payload: dict[str, Any]) -> str | None:
    explicit = payload.get("domain")
    if isinstance(explicit, str) and explicit:
        return explicit
    root = None
    try:
        root = origin(task, payload)
    except UnsolvableError:
        return None
    return urlparse(root).hostname


def js_timeout_ms(task: Task, payload: dict[str, Any]) -> int:
    value = payload.get("js_timeout_ms")
    seconds = float(value) / 1000.0 if isinstance(value, int | float) and value > 0 else DEFAULT_JS_TIMEOUT_MS / 1000.0
    return max(1, int(_deadline_capped(task, seconds) * 1000))


def cookie_timeout_s(task: Task, payload: dict[str, Any]) -> float:
    value = payload.get("timeout_s")
    seconds = float(value) if isinstance(value, int | float) and value >= 0 else DEFAULT_COOKIE_TIMEOUT_S
    return _deadline_capped(task, seconds)


def _deadline_capped(task: Task, seconds: float) -> float:
    deadline = getattr(task, "deadline_ts", None)
    if deadline:
        remaining = deadline - time.time()
        if remaining > 0:
            return min(seconds, remaining)
    return seconds


__all__ = [
    "DEFAULT_COOKIE_TIMEOUT_S",
    "DEFAULT_JS_TIMEOUT_MS",
    "challenge_js",
    "cookie_timeout_s",
    "domain",
    "js_timeout_ms",
    "nonce",
    "origin",
    "payload_cookies",
    "reese_script_url",
    "user_agent",
    "version",
]
