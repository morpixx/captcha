"""JSONP and status handling shared by the GeeTest v3/v4 solvers.

Both generations answer 'callback=geetest_<ms>({...})' and wrap the useful
object in 'data'. A non-success 'status' means different things per call:
stale challenge on load/round endpoints, wrong answer or rejected w on verify.
"""

from __future__ import annotations

import json
from typing import Any

from captcha.domain.errors import CaptchaError, ChallengeExpiredError
from captcha.domain.task import Task

_EXPIRED_HINTS = ("expire", "timeout", "stale", "过期", "超时", "失效", "invalid challenge", "not found")


def request_headers(task: Task) -> dict[str, str]:
    """Browser-ish headers the widget sends; Referer is the site, not geetest."""
    page = task.site_url or str(task.payload.get("pageurl") or task.payload.get("websiteURL") or "")
    headers = {"Accept": "*/*", "Accept-Language": task.language or "en"}
    if page:
        headers["Referer"] = page
    if task.user_agent:
        headers["User-Agent"] = task.user_agent
    return headers


def jsonp(text: str, generation: str) -> dict[str, Any]:
    """Parse a plain or JSONP-wrapped body into a dict."""
    body = text.strip()
    if not body.startswith("{"):
        start, end = body.find("("), body.rfind(")")
        if start != -1 and end > start:
            body = body[start + 1 : end]
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise CaptchaError(
            f"geetest {generation}: response is not JSON",
            context={"head": text[:160]},
        ) from exc
    if not isinstance(parsed, dict):
        raise CaptchaError(
            f"geetest {generation}: unexpected response shape",
            context={"type": type(parsed).__name__},
        )
    return parsed


def inner(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the 'data' object; fall back to the payload itself."""
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def check_status(
    payload: dict[str, Any],
    generation: str,
    where: str,
    *,
    stale_on_fail: bool,
) -> None:
    """Raise the canonical error for a non-success status.

    'stale_on_fail' is true on load/round calls, where any failure means the
    client-supplied challenge is no longer usable. On verify it stays false:
    only an explicit expiry hint is a stale challenge, everything else is a
    rejected answer.
    """
    status = str(payload.get("status") or "").lower()
    if status in ("", "success", "ok"):
        return
    blob = f"{status} {payload.get('error') or ''} {payload.get('message') or ''}".lower()
    if stale_on_fail or any(hint in blob for hint in _EXPIRED_HINTS):
        raise ChallengeExpiredError(
            f"geetest {generation} {where} rejected the challenge",
            context={"status": status, "error": payload.get("error") or payload.get("message") or ""},
        )
    raise CaptchaError(
        f"geetest {generation} {where} failed",
        context={"status": status, "keys": sorted(payload)},
    )
