"""Payload coercion for the Castle solver.

Castle payloads are thin: a page URL, the publishable key, and sometimes the SDK
source or an already-minted token. The spellings clients use for each are folded
here so the solver reads like its three paths.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from captcha.domain import Task
from captcha.domain.errors import UnsolvableError

MAX_URL_LEN = 2048
DEFAULT_COOKIE_TIMEOUT_S = 20.0


def page_url(task: Task, payload: dict[str, Any]) -> str:
    rejected: str | None = None
    for value in (
        task.site_url,
        payload.get("websiteURL"),
        payload.get("websiteUrl"),
        payload.get("pageurl"),
        payload.get("page_url"),
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
            "castle websiteURL must be a short http(s) URL",
            context={"length": len(rejected)},
        )
    raise UnsolvableError(
        "castle needs the page the SDK runs on (websiteURL)",
        context={"type": task.type.value},
    )


def publishable_key(task: Task, payload: dict[str, Any]) -> str:
    value = payload.get("publishable_key") or payload.get("pk") or payload.get("castle_pk")
    if not value:
        raise UnsolvableError(
            "castle requires the publishable key (pk_...) in payload",
            context={"type": task.type.value},
        )
    return str(value).strip()


def supplied_token(payload: dict[str, Any]) -> str:
    """A token the client already minted (or a mock token for its own tests)."""
    for key in ("castle_request_token", "request_token", "token"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def challenge_js(payload: dict[str, Any]) -> str:
    for key in ("challenge_js", "castle_js", "sdk_js", "script"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def sdk_url(payload: dict[str, Any], default: str) -> str:
    """SDK source URL the page should load when the SDK is not already there."""
    for key in ("castle_js_url", "sdk_url", "script_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
            return value.strip()
    return default


def user_agent(task: Task, payload: dict[str, Any]) -> str | None:
    value = task.user_agent or payload.get("userAgent") or payload.get("user_agent") or payload.get("ua")
    return str(value) if value else None


def domain(task: Task, payload: dict[str, Any]) -> str | None:
    explicit = payload.get("domain")
    if isinstance(explicit, str) and explicit:
        return explicit
    try:
        host = urlparse(page_url(task, payload)).hostname
    except UnsolvableError:
        return None
    return host


def cookie_timeout_s(task: Task, payload: dict[str, Any]) -> float:
    value = payload.get("timeout_s")
    seconds = float(value) if isinstance(value, int | float) and value >= 0 else DEFAULT_COOKIE_TIMEOUT_S
    deadline = getattr(task, "deadline_ts", None)
    if deadline:
        remaining = deadline - time.time()
        if remaining > 0:
            return min(seconds, remaining)
    return seconds


__all__ = [
    "DEFAULT_COOKIE_TIMEOUT_S",
    "challenge_js",
    "cookie_timeout_s",
    "domain",
    "page_url",
    "publishable_key",
    "sdk_url",
    "supplied_token",
    "user_agent",
]
