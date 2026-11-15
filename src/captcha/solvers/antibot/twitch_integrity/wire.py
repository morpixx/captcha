"""Twitch Client-Integrity token: decode, validate, harvest.

A Client-Integrity token is v4.public.<base64url(payload_json || 64-byte
signature)>. The payload is plain JSON; the signature is not verifiable
without Twitch's key, so all we can do is refuse malformed and flagged
tokens instead of handing a client something the site will score as a bot.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from captcha.domain.errors import ChallengeExpiredError, SensorError
from captcha.engines.twitch.builder import INTEGRITY_PREFIX, SIGNATURE_BYTES


def capture_headers(raw: Any) -> dict[str, str]:
    """Headers the KPSDK bootstrap attached to the integrity fetch.

    The builder's driver returns JSON; a runtime that hands back a dict is
    accepted too. An explicit driver error means the bootstrap threw before it
    patched fetch, which is a sensor failure, not an empty answer.
    """
    data = raw
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SensorError(
                "twitch integrity driver returned non-JSON",
                context={"sample": data[:120]},
            ) from exc
    if not isinstance(data, dict):
        raise SensorError(
            "twitch integrity driver returned an unexpected type",
            context={"got": type(data).__name__},
        )
    error = data.get("error")
    if error:
        raise SensorError(
            "twitch KPSDK bootstrap failed in the JS runtime",
            context={"error": str(error)[:300], "errors": data.get("errors") or []},
        )
    request = data.get("request")
    headers = request.get("headers") if isinstance(request, dict) else data.get("headers")
    if not isinstance(headers, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items() if v is not None}


def merge_headers(base: dict[str, str], captured: dict[str, str]) -> dict[str, str]:
    """KPSDK headers win over the caller's defaults for the same name."""
    merged = {str(k): str(v) for k, v in base.items()}
    for name, value in captured.items():
        if value:
            merged[name] = value
    return merged


def decode_claims(token: str) -> dict[str, Any]:
    """Strip the v4 envelope and the trailing signature, then parse the JSON."""
    if not isinstance(token, str) or not token.startswith(INTEGRITY_PREFIX):
        raise SensorError(
            "twitch client-integrity token has an unexpected envelope",
            context={"prefix": str(token)[:16]},
        )
    body = token[len(INTEGRITY_PREFIX) :].replace("-", "+").replace("_", "/")
    body += "=" * ((4 - len(body) % 4) % 4)
    try:
        blob = base64.b64decode(body)
    except (ValueError, TypeError) as exc:
        raise SensorError("twitch client-integrity token is not base64url") from exc
    if len(blob) <= SIGNATURE_BYTES:
        raise SensorError(
            "twitch client-integrity token is shorter than its signature",
            context={"bytes": len(blob)},
        )
    payload = blob[: -SIGNATURE_BYTES]
    try:
        claims = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SensorError(
            "twitch client-integrity payload is not JSON",
            context={"sample": payload[:80].decode("utf-8", "replace")},
        ) from exc
    if not isinstance(claims, dict):
        raise SensorError(
            "twitch client-integrity payload is not an object",
            context={"got": type(claims).__name__},
        )
    return claims


def flagged(claims: dict[str, Any]) -> bool:
    """True when Twitch marked the token as bot traffic.

    The claim is a string in the wild ("false"), but a bool or a numeric zero
    is accepted so a server-side type change does not turn every token into a
    false positive.
    """
    value = claims.get("is_bad_bot")
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in ("false", "0", "")


def parse_response(raw: Any) -> tuple[str, int | None]:
    """Pull (token, expiration_ms) out of the /integrity body.

    A 200 with no token is an expired challenge: Twitch returns an empty
    object when it refuses to mint, and retrying with the same device id can
    work once the client refreshes its session.
    """
    payload = _body(raw)
    data = payload
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SensorError(
                "twitch /integrity returned non-JSON",
                context={"sample": data[:120]},
            ) from exc
    if not isinstance(data, dict):
        raise SensorError(
            "twitch /integrity returned an unexpected type",
            context={"got": type(data).__name__},
        )
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        raise ChallengeExpiredError(
            "twitch /integrity minted no token; the session is stale",
            context={"keys": sorted(data)[:12], "error": data.get("error")},
        )
    expiration = data.get("expiration")
    if isinstance(expiration, str) and expiration.isdigit():
        expiration = int(expiration)
    return token.strip(), expiration if isinstance(expiration, int) else None


def _body(raw: Any) -> Any:
    """Accept a transport Response, bytes, str or an already-decoded dict."""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace")
    if isinstance(raw, (str, dict)):
        return raw
    body = getattr(raw, "body", None)
    if body is not None:
        return body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else body
    json_method = getattr(raw, "json", None)
    if callable(json_method):
        return json_method()
    raise SensorError(
        "twitch /integrity response is not readable",
        context={"got": type(raw).__name__},
    )
