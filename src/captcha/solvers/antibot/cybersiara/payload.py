"""Payload coercion for the CyberSiARA solver.

The site key arrives under four different names depending on which client built
the task (`sitekey`, `SlideMasterUrlId`, `MasterUrlId`, `master_url_id`), and
the page under the usual aliases. Folding them here keeps the solver about its
paths.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import Task
from captcha.domain.errors import UnsolvableError

DEFAULT_TIMEOUT_S = 20.0


def sitekey(task: Task, payload: dict[str, Any]) -> str:
    for value in (
        task.sitekey,
        payload.get("sitekey"),
        payload.get("SlideMasterUrlId"),
        payload.get("MasterUrlId"),
        payload.get("master_url_id"),
    ):
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text
    raise UnsolvableError(
        "cybersiara requires the site key (SlideMasterUrlId)",
        context={"type": task.type.value},
    )


def supplied_token(payload: dict[str, Any]) -> str:
    for key in ("master_url_token", "MasterUrlToken", "token"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def script(payload: dict[str, Any]) -> str:
    for key in ("challenge_js", "cybersiara_js", "widget_js", "script"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def site(task: Task, payload: dict[str, Any]) -> str:
    for value in (task.site_url, payload.get("websiteURL"), payload.get("pageurl"), payload.get("url")):
        if isinstance(value, str) and value.strip().lower().startswith(("http://", "https://")):
            return value.strip()
    raise UnsolvableError(
        "cybersiara needs the page URL (websiteURL)",
        context={"type": task.type.value},
    )


def user_agent(task: Task, payload: dict[str, Any]) -> str | None:
    value = task.user_agent or payload.get("userAgent") or payload.get("ua")
    return str(value) if value else None


def timeout_s(task: Task, payload: dict[str, Any]) -> float:
    value = payload.get("timeout_s")
    seconds = float(value) if isinstance(value, int | float) and value >= 0 else DEFAULT_TIMEOUT_S
    deadline = getattr(task, "deadline_ts", None)
    if deadline:
        remaining = deadline - time.time()
        if remaining > 0:
            return min(seconds, remaining)
    return seconds


__all__ = ["DEFAULT_TIMEOUT_S", "script", "site", "sitekey", "supplied_token", "timeout_s", "user_agent"]
