"""Payload coercion for the BotDeflector solver.

CapSolver's AntiBotdeflectorTaskProxyLess payload is websiteURL + domain +
flowToken; on the page the token is called botDeflectorJwtToken (and in some
integrations botReflectorJwtToken). domain is the API host and is not
required to match websiteURL -- on Queue-it softblocks it does not.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from captcha.domain import Task
from captcha.engines.botdeflector.builder import DEFAULT_DOMAIN, QUEUE_IT_HOST

_MAX_URL_LEN = 2048


def _first(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def flow_token(payload: dict[str, Any]) -> str:
    return _first(
        payload,
        "flowToken",
        "flow_token",
        "botDeflectorJwtToken",
        "botReflectorJwtToken",
        "botdeflector_jwt_token",
    )


def challenge_result(payload: dict[str, Any]) -> str:
    """A previously solved ChallengeFlowResult, short-circuits the flow."""
    return _first(payload, "tt_solutions", "challenge_flow_result", "challengeFlowResult", "solution_token")


def site_key(payload: dict[str, Any]) -> str:
    """Classic-mode site key from the widget embed; Queue-it has none."""
    return _first(payload, "siteKey", "site_key")


def account_id(payload: dict[str, Any]) -> str:
    """Classic-mode account id from the widget embed; Queue-it carries it in the JWT."""
    return _first(payload, "accountId", "account_id")


def domain_for(task: Task, payload: dict[str, Any]) -> str:
    """Domain, defaulting to Queue-it when the page is a softblock."""
    raw = _first(payload, "domain", "challenge_domain", "api_domain")
    if raw:
        return normalize_domain(raw)
    for value in (task.site_url, payload.get("websiteURL"), payload.get("pageurl")):
        if isinstance(value, str) and "queue-it" in value:
            return QUEUE_IT_HOST
    return DEFAULT_DOMAIN


def normalize_domain(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    if len(value) > _MAX_URL_LEN:
        return DEFAULT_DOMAIN
    parsed = urlparse(value)
    if not parsed.hostname:
        return DEFAULT_DOMAIN
    return f"{parsed.scheme}://{parsed.netloc}"


def website_url(task: Task, payload: dict[str, Any]) -> str:
    for value in (task.site_url, payload.get("websiteURL"), payload.get("pageurl"), payload.get("url")):
        if isinstance(value, str) and value.strip():
            url = value.strip()
            if len(url) <= _MAX_URL_LEN and url.lower().startswith(("http://", "https://")):
                return url
    return ""


def fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("fingerprint")
    return dict(raw) if isinstance(raw, dict) else {}


def user_agent(task: Task, payload: dict[str, Any]) -> str:
    value = task.user_agent or _first(payload, "userAgent", "user_agent", "ua")
    return value


def headers(payload: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("headers")
    if not isinstance(raw, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in raw.items() if v is not None}
