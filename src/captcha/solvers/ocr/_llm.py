"""LLM transport for text_captcha: endpoint policy, retries, response decoding.

Kept out of the solver file so each module has one job and stays under the size
cap. Endpoint and credentials are settings-only: a per-task endpoint override is
accepted only when it matches the configured origin, so a client cannot point the
worker at an arbitrary host, and a per-task api_key is ignored so secrets never
land in a stored task payload.
"""

from __future__ import annotations

import ipaddress
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from captcha.domain.errors import CaptchaError, ConfigError, ModelInferenceError
from captcha.domain.errors import TimeoutError as CaptchaTimeout

SYS_PROMPT = (
    "You solve text CAPTCHA puzzles. Return ONLY the answer - no prose, no reasoning, "
    "no punctuation, no quotes, no leading label. If the answer is a number, return digits. "
    "If it's a word, return the single word in lowercase. Nothing else."
)

REQUEST_TIMEOUT_SEC = 30.0
MAX_ATTEMPTS = 2
RETRY_MIN_BUDGET_SEC = 1.0
MAX_MODEL_CHARS = 128
METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal", "100.100.100.200"})
_ANSWER_PREFIX = re.compile(r"^(?:the\s+)?(?:final\s+)?(?:answer|response|result)\s*(?:is|:)\s*", re.IGNORECASE)


def normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1/chat/completions"):
        return endpoint
    base = endpoint if endpoint.endswith("/v1") else f"{endpoint}/v1"
    return f"{base}/chat/completions"


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    return scheme, (parsed.hostname or "").lower(), parsed.port or (443 if scheme == "https" else 80)


def validate_endpoint(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigError("text_captcha: LLM endpoint must be an absolute http(s) URL", context={"endpoint": url})
    if parsed.username or parsed.password:
        raise ConfigError("text_captcha: LLM endpoint must not embed credentials", context={"endpoint": url})
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ConfigError("text_captcha: LLM endpoint has an invalid port", context={"endpoint": url}) from exc
    host = parsed.hostname.lower()
    if host in METADATA_HOSTS:
        raise ConfigError("text_captcha: LLM endpoint points at a metadata host", context={"endpoint": url})
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise ConfigError("text_captcha: LLM endpoint points at a non-routable address", context={"endpoint": url})
    return url


def resolve_endpoint(settings: Any, payload: dict[str, Any]) -> str:
    configured = str(getattr(settings, "llm_endpoint", "") or "").strip()
    configured = validate_endpoint(configured) if configured else ""
    override = payload.get("endpoint")
    if isinstance(override, str) and override.strip():
        override = validate_endpoint(override.strip())
        if not configured:
            # Without a configured origin there is nothing to compare the
            # override against, and a client-supplied host would turn the
            # solver into an SSRF sink (or ship the settings api_key to it).
            raise ConfigError(
                "text_captcha: per-task endpoint override requires LLM_ENDPOINT to be configured",
                context={"override": _origin(override)},
            )
        if _origin(override) != _origin(configured):
            raise ConfigError(
                "text_captcha: per-task endpoint override must match LLM_ENDPOINT",
                context={"configured": _origin(configured), "override": _origin(override)},
            )
        return normalize_endpoint(override)
    if not configured:
        raise ConfigError("text_captcha: LLM endpoint not configured (LLM_ENDPOINT)")
    return normalize_endpoint(configured)


def resolve_model(settings: Any, payload: dict[str, Any]) -> str:
    model = str(payload.get("model") or getattr(settings, "llm_model", "") or "").strip()
    if not model:
        raise ConfigError("text_captcha: LLM model not configured (LLM_MODEL)")
    if len(model) > MAX_MODEL_CHARS:
        raise ConfigError("text_captcha: model name is too long", context={"length": len(model)})
    return model


def strip_answer(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    line = _ANSWER_PREFIX.sub("", line)
    line = line.strip().strip('"').strip("'").strip()
    return line.rstrip(".,;:!?").strip()


def status_of(resp: Any) -> int:
    value = getattr(resp, "status_code", None)
    if value is None:
        value = getattr(resp, "status", 0)
    return int(value)


def _body_text(resp: Any) -> str:
    text = getattr(resp, "text", "")
    if callable(text):
        text = text()
    return str(text)[:400]


def response_content(resp: Any, url: str) -> str:
    try:
        data = resp.json()
    except (ValueError, TypeError) as exc:
        raise ModelInferenceError("text_captcha: LLM response is not JSON", context={"endpoint": url}) from exc
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelInferenceError(
            "text_captcha: malformed LLM response (no choices[0])", context={"endpoint": url}
        ) from exc
    content: Any = None
    if isinstance(choice, dict):
        message = choice.get("message") or {}
        if isinstance(message, dict):
            content = message.get("content")
        if content is None:
            content = choice.get("text")
    if isinstance(content, list):
        content = " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    if not isinstance(content, str):
        raise ModelInferenceError("text_captcha: LLM response has no message content", context={"endpoint": url})
    return content


def http_error(status: int, body: str, url: str) -> CaptchaError:
    if status in (401, 403, 407):
        return ConfigError(
            f"text_captcha: LLM endpoint rejected credentials (HTTP {status})", context={"endpoint": url}
        )
    if status == 404:
        return ConfigError(
            "text_captcha: LLM endpoint not found (HTTP 404) - check LLM_ENDPOINT", context={"endpoint": url}
        )
    if status == 429:
        return ModelInferenceError("text_captcha: LLM endpoint is rate-limited (HTTP 429)", context={"endpoint": url})
    return ModelInferenceError(
        f"text_captcha: LLM endpoint HTTP {status}: {body[:200]}",
        context={"endpoint": url, "status": status},
    )


def task_deadline(deadline_ts: float | None) -> float:
    if deadline_ts is None:
        return time.monotonic() + REQUEST_TIMEOUT_SEC
    remaining = deadline_ts - time.time()
    if remaining <= 0:
        raise CaptchaTimeout("text_captcha: task deadline already passed", context={"deadline_ts": deadline_ts})
    return time.monotonic() + remaining


async def chat(
    client: Any,
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    deadline: float,
) -> str:
    """POST one chat completion. Retries 5xx/transport once while the budget allows."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptchaTimeout("text_captcha: LLM call exceeded the task deadline")
        timeout = min(REQUEST_TIMEOUT_SEC, remaining)
        try:
            resp = await client.post(url, json=body, headers=headers, timeout=timeout)
        except CaptchaError as exc:
            status = int((getattr(exc, "context", None) or {}).get("status") or 0)
            if status:
                raise http_error(status, str(exc), url) from exc
            raise
        except (httpx.HTTPError, TimeoutError, ConnectionError, OSError) as exc:
            if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                raise CaptchaTimeout(f"text_captcha: LLM endpoint timed out ({url})") from exc
            error: CaptchaError = ModelInferenceError(
                f"text_captcha: LLM endpoint network error: {exc}",
                context={"endpoint": url},
            )
            if attempt < MAX_ATTEMPTS and deadline - time.monotonic() > RETRY_MIN_BUDGET_SEC:
                continue
            raise error from exc

        status = status_of(resp)
        if status < 400:
            return response_content(resp, url)
        if status >= 500 and attempt < MAX_ATTEMPTS and deadline - time.monotonic() > RETRY_MIN_BUDGET_SEC:
            continue
        raise http_error(status, _body_text(resp), url)
    raise ModelInferenceError("text_captcha: LLM call produced no answer", context={"endpoint": url})
