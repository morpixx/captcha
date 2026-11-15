"""Payload coercion for the PerimeterX solver.

2captcha payloads for this family arrive with the appId under `app_id`,
`px_app_id` or `pxAppId`, the page under the usual aliases, and sometimes a
collector URL that is not the default host. Folding the spellings here keeps the
solver about its two round types.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain import Task
from captcha.domain.errors import UnsolvableError
from captcha.solvers.antibot._runtime import cookies_to_dict

DEFAULT_JS_TIMEOUT_MS = 15_000
DEFAULT_COOKIE_TIMEOUT_S = 20.0
MAX_URL_LEN = 2048


def app_id(task: Task, payload: dict[str, Any]) -> str:
    for key in ("app_id", "px_app_id", "pxAppId", "px_appId"):
        value = payload.get(key)
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text
    raise UnsolvableError(
        "perimeterx requires the site's appId (PX...)",
        context={"type": task.type.value, "keys": sorted(payload)[:12]},
    )


def site_url(task: Task, payload: dict[str, Any]) -> str:
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
        if len(url) <= MAX_URL_LEN and url.lower().startswith(("http://", "https://")):
            return url
        rejected = rejected or url
    if rejected is not None:
        raise UnsolvableError(
            "perimeterx websiteURL must be a short http(s) URL",
            context={"length": len(rejected)},
        )
    raise UnsolvableError(
        "perimeterx needs the page the sensor was taken on (websiteURL)",
        context={"type": task.type.value},
    )


def collector_override(payload: dict[str, Any]) -> str:
    for key in ("collector_url", "collector", "px_collector_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
            return value.strip()
    return ""


def user_agent(task: Task, payload: dict[str, Any]) -> str | None:
    value = task.user_agent or payload.get("userAgent") or payload.get("user_agent") or payload.get("ua")
    return str(value) if value else None


def cookies(payload: dict[str, Any]) -> dict[str, str]:
    """Cookies of a previous round: `_px`, `_pxvid`, `_pxhd`."""
    jar = cookies_to_dict(payload.get("cookies"))
    for name in ("_px", "_pxhd", "_pxvid"):
        value = payload.get(name)
        if isinstance(value, str) and value:
            jar.setdefault(name, value)
    return jar


def script(payload: dict[str, Any]) -> str:
    """The press bundle source, under any of the names clients use."""
    for key in ("challenge_js", "bundle_js", "press_js", "script"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def tag(payload: dict[str, Any]) -> str:
    value = payload.get("tag")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "mobile" if payload.get("mobile") else "web"


def uuid(payload: dict[str, Any]) -> str:
    for key in ("uuid", "vid", "_pxvid"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def builder_extra(task: Task, payload: dict[str, Any], app_id: str, *, ua: str | None) -> dict[str, Any]:
    """Everything the builder needs, in one dict (it reads `ctx.extra`)."""
    return {
        "app_id": app_id,
        "tag": tag(payload),
        "uuid": uuid(payload),
        "vid": payload.get("vid"),
        "sid": payload.get("sid"),
        "pc_len": payload.get("pc_len"),
        "ua": ua,
    }


def domain(task: Task, payload: dict[str, Any]) -> str | None:
    explicit = payload.get("domain")
    if isinstance(explicit, str) and explicit:
        return explicit
    try:
        return urlparse(site_url(task, payload)).hostname
    except UnsolvableError:
        return None


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
    "app_id",
    "builder_extra",
    "collector_override",
    "cookie_timeout_s",
    "cookies",
    "domain",
    "js_timeout_ms",
    "script",
    "site_url",
    "tag",
    "user_agent",
    "uuid",
]
