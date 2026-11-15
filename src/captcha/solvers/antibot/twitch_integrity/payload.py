"""Payload coercion for the Twitch Integrity solver.

Clients send the same handful of values under several spellings: the device
id as device_id/deviceId/x-device-id, a session id, a client version, an
optional OAuth token, and either an inlined KPSDK bootstrap or a URL to it.
Everything below normalizes those once, so the solver reads like the flow it
has and not like a pile of dict.get fallbacks.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import Task
from captcha.domain.errors import UnsolvableError
from captcha.engines.twitch.builder import DEFAULT_PAGE_URL, KPSDK_SCRIPT_URL, WEB_CLIENT_ID

DEFAULT_JS_TIMEOUT_MS = 20_000
_MAX_URL_LEN = 2048


def _first(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def device_id(payload: dict[str, Any]) -> str:
    return _first(payload, "device_id", "deviceId", "x_device_id", "x-device-id", "device")


def client_session_id(payload: dict[str, Any]) -> str:
    return _first(payload, "client_session_id", "clientSessionId", "client-session-id", "session_id")


def client_version(payload: dict[str, Any]) -> str:
    return _first(payload, "client_version", "clientVersion", "client-version")


def client_request_id(payload: dict[str, Any]) -> str:
    return _first(payload, "client_request_id", "clientRequestId", "client-request-id")


def client_id(payload: dict[str, Any]) -> str:
    return _first(payload, "client_id", "clientId", "client-id") or WEB_CLIENT_ID


def oauth(payload: dict[str, Any]) -> str:
    """OAuth token without the "OAuth " prefix; empty when anonymous."""
    raw = _first(payload, "oauth", "oauth_token", "access_token", "authorization")
    if raw.lower().startswith("oauth "):
        raw = raw[6:].strip()
    return raw


def client_integrity(payload: dict[str, Any]) -> str:
    """A previously minted Client-Integrity token, short-circuits the flow."""
    return _first(payload, "client_integrity", "clientIntegrity", "integrity_token", "integrity")


def challenge_js(payload: dict[str, Any]) -> str:
    """KPSDK bootstrap inlined by the client."""
    for key in ("challenge_js", "p_script", "kpsdk_js"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def script_url(payload: dict[str, Any]) -> str:
    """Where to fetch the bootstrap when it was not inlined."""
    value = _first(payload, "p_script_url", "script_url", "challenge_js_url", "kpsdk_url")
    return value or KPSDK_SCRIPT_URL


def page_url(task: Task, payload: dict[str, Any]) -> str:
    for value in (task.site_url, payload.get("websiteURL"), payload.get("pageurl"), payload.get("url")):
        if not isinstance(value, str) or not value.strip():
            continue
        url = value.strip()
        if len(url) <= _MAX_URL_LEN and url.lower().startswith(("http://", "https://")):
            return url
        raise UnsolvableError(
            "twitch_integrity websiteURL must be a short http(s) URL",
            context={"length": len(url)},
        )
    return DEFAULT_PAGE_URL


def user_agent(task: Task, payload: dict[str, Any]) -> str:
    value = task.user_agent or _first(payload, "userAgent", "user_agent", "ua")
    return value


def headers(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("headers")
    if not isinstance(raw, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in raw.items() if v is not None}


def request_headers(task: Task, payload: dict[str, Any]) -> dict[str, str]:
    """The header set the Twitch frontend sends to /integrity.

    client-id and a device id are the only ones the endpoint cannot derive;
    the rest are echoed when the client has them.
    """
    out: dict[str, str] = {"client-id": client_id(payload)}
    for name, value in (
        ("x-device-id", device_id(payload)),
        ("client-session-id", client_session_id(payload)),
        ("client-version", client_version(payload)),
        ("client-request-id", client_request_id(payload)),
        ("user-agent", user_agent(task, payload)),
    ):
        if value:
            out[name] = value
    token = oauth(payload)
    if token:
        out["authorization"] = f"OAuth {token}"
    out.update(headers(payload))
    return out


def timestamp_ms(payload: dict[str, Any]) -> int:
    value = payload.get("timestamp_ms")
    if isinstance(value, int) and value > 0:
        return value
    return int(time.time() * 1000)


def js_timeout_ms(task: Task, payload: dict[str, Any]) -> int:
    value = payload.get("js_timeout_ms")
    millis = int(value) if isinstance(value, (int, float)) and value > 0 else DEFAULT_JS_TIMEOUT_MS
    deadline = getattr(task, "deadline_ts", None)
    if deadline:
        remaining = deadline - time.time()
        if remaining > 0:
            millis = min(millis, int(remaining * 1000))
    return max(1, millis)
