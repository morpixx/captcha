"""Payload coercion for the BotGuard solver.

The 2captcha-style payload is loose: the program arrives as ftx or program,
the interpreter either inline or as a URL (a product-scoped host), and the
product picks which Google endpoint would consume the token. One normalizer
so the solver reads like the VM flow and not like dict.get fallbacks.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import Task
from captcha.engines.botguard.builder import GOOGLE_HOST

DEFAULT_JS_TIMEOUT_MS = 30_000
_MAX_URL_LEN = 2048


def _first(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def program_raw(payload: dict[str, Any]) -> str:
    """The f.txt program, bare or JSON-wrapped (wire.parse_program decides)."""
    return _first(payload, "ftx", "program", "bg_program", "challenge_program", "bg_challenge")


def interpreter_js(payload: dict[str, Any]) -> str:
    for key in ("interpreter_js", "interpreterJavascript", "interpreter", "challenge_js", "challenge_script"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def interpreter_url(payload: dict[str, Any]) -> str:
    raw = _first(payload, "interpreter_url", "interpreterUrl", "challenge_js_url", "program_url")
    return normalize_url(raw) if raw else ""


def normalize_url(raw: str) -> str:
    """WAA returns a protocol-relative URL; reCAPTCHA a path on its own host."""
    value = raw.strip()
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return GOOGLE_HOST + value
    if len(value) <= _MAX_URL_LEN and value.lower().startswith(("http://", "https://")):
        return value
    return ""


def global_name(payload: dict[str, Any]) -> str:
    return _first(payload, "global_name", "globalName", "vm_global")


def product(payload: dict[str, Any]) -> str:
    return _first(payload, "product", "surface", "botguard_product") or "recaptcha"


def user_agent(task: Task, payload: dict[str, Any]) -> str:
    value = task.user_agent or _first(payload, "userAgent", "user_agent", "ua")
    return value


def headers(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("headers")
    if not isinstance(raw, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in raw.items() if v is not None}


def cookies(payload: dict[str, Any]) -> dict[str, str]:
    """Cookies as a map, or a "a=b; c=d" string from a page dump."""
    raw = payload.get("cookies")
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if v is not None}
    if isinstance(raw, str):
        out: dict[str, str] = {}
        for pair in raw.split(";"):
            name, sep, value = pair.strip().partition("=")
            if sep and name:
                out[name] = value
        return out
    return {}


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
            millis = min(millis, max(1, int(remaining * 1000)))
    return max(1, millis)
