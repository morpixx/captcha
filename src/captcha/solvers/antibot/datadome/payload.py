"""Payload coercion for the DataDome solver.

The 2captcha payload for this family is loose: clients send the page URL under
``websiteURL``/``pageurl``/``url``, the key as ``ddjskey``/``ddk``/``sitekey``,
cookies as a map/string/list. Everything below normalizes those spellings once,
so the solver reads like the three paths it actually has and not like a pile of
``dict.get`` fallbacks.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import UnsolvableError

from . import endpoints, interstitial

DEFAULT_JS_TIMEOUT_MS = 15_000
DEFAULT_COOKIE_TIMEOUT_S = 15.0
_MAX_URL_LEN = 2048


def payload_cookie(payload: dict[str, Any]) -> str:
    """Cookie of a previous round, supplied by the client."""
    jar = interstitial.cookies_to_dict(payload.get("cookies"))
    value = payload.get("datadome") or jar.get(endpoints.DATADOME_COOKIES[0])
    return str(value) if value else ""


def user_agent(task: Task, payload: dict[str, Any]) -> str | None:
    value = task.user_agent or payload.get("userAgent") or payload.get("user_agent") or payload.get("ua")
    return str(value) if value else None


def page_url(task: Task, payload: dict[str, Any]) -> str:
    rejected: str | None = None
    for value in (
        task.site_url,
        payload.get("websiteURL"),
        payload.get("websiteUrl"),
        payload.get("pageurl"),
        payload.get("url"),
    ):
        if not isinstance(value, str) or not value.strip():
            continue
        url = value.strip()
        if len(url) <= _MAX_URL_LEN and url.lower().startswith(("http://", "https://")):
            return url
        rejected = rejected or url
    if rejected is not None:
        raise UnsolvableError(
            "datadome websiteURL must be a short http(s) URL",
            context={"length": len(rejected)},
        )
    raise UnsolvableError(
        "payload missing websiteURL: datadome needs the page the sensor was taken on",
        context={"type": task.type.value},
    )


def domain(task: Task, payload: dict[str, Any]) -> str | None:
    explicit = payload.get("domain")
    if isinstance(explicit, str) and explicit:
        return explicit
    for value in (task.site_url, payload.get("websiteURL"), payload.get("pageurl"), payload.get("url")):
        if isinstance(value, str) and value:
            host = urlparse(value if "//" in value else "https://" + value).hostname
            if host:
                return host
    return None


def ddjskey(task: Task, payload: dict[str, Any]) -> str:
    value = payload.get("ddjskey") or payload.get("ddk") or payload.get("sitekey") or task.sitekey
    return str(value) if value else ""


def signals(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("signals", "parsed_body", "sensor"):
        value = payload.get(key)
        if isinstance(value, dict) and value:
            return dict(value)
    return {}


def js_type(task: Task) -> str:
    return "cs" if task.type is CaptchaType.DATADOME_SLIDER else "ch"


def timestamp_ms(payload: dict[str, Any]) -> int:
    value = payload.get("timestamp_ms")
    if isinstance(value, int) and value > 0:
        return value
    return int(time.time() * 1000)


def js_globals(task: Task, payload: dict[str, Any]) -> dict[str, Any]:
    """Globals the captured challenge script runs with (JSON-serializable only)."""
    raw_cookies = payload.get("cookies")
    out: dict[str, Any] = {}
    for name, value in (
        ("ddjskey", ddjskey(task, payload)),
        ("cid", payload.get("cid")),
        ("hsh", payload.get("hsh")),
        ("href", task.site_url or payload.get("websiteURL")),
        ("userAgent", user_agent(task, payload)),
        ("cookies", raw_cookies if isinstance(raw_cookies, str) else None),
        ("challengeUrl", payload.get("challenge_url")),
    ):
        if value:
            out[name] = value
    return out


def js_timeout_ms(task: Task, payload: dict[str, Any]) -> int:
    value = payload.get("js_timeout_ms")
    seconds = float(value) / 1000.0 if isinstance(value, (int, float)) and value > 0 else DEFAULT_JS_TIMEOUT_MS / 1000.0
    return max(1, int(_deadline_capped(task, seconds) * 1000))


def cookie_timeout_s(task: Task, payload: dict[str, Any]) -> float:
    value = payload.get("timeout_s")
    seconds = float(value) if isinstance(value, (int, float)) and value >= 0 else DEFAULT_COOKIE_TIMEOUT_S
    return _deadline_capped(task, seconds)


def _deadline_capped(task: Task, seconds: float) -> float:
    deadline = getattr(task, "deadline_ts", None)
    if deadline:
        remaining = deadline - time.time()
        if remaining > 0:
            return min(seconds, remaining)
    return seconds
