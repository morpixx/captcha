"""BotDeflector flow token, PoW payloads and step tokens.

The flow token is a plain JWT (signature not verifiable by us) whose payload
says which steps run, in order, and which session they belong to:

    {"accountId": ..., "siteKey": ..., "sessionId": ...,
     "challengeFlow": [{"type": "inv", "complexity": 1},
                       {"type": "icon", "complexity": 1}],
     "iat": ..., "exp": ...}

Everything below is deliberately tolerant about key spelling -- Queue-it and
standalone deployments have shipped both camelCase and snake_case -- but
strict about expiry, because a flow token is single-use and an expired one
must not cost a solve.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any

from captcha.domain.errors import ChallengeExpiredError, SensorError, UnsolvableError
from captcha.engines.botdeflector import pow

#: Step kinds that are the invisible proof of work; anything else needs a
#: visual solver and is refused by this HTTP path.
INVISIBLE_KINDS: frozenset[str] = frozenset({"inv", "invisible", "pow"})
ICON_KIND: str = "icon"

#: A sane flow is short; a token claiming thousands of steps is malformed.
MAX_FLOW_STEPS = 64


@dataclass(frozen=True, slots=True)
class FlowToken:
    raw: str
    claims: dict[str, Any]
    steps: tuple[str, ...]
    session_id: str
    account_id: str
    site_key: str
    expires_at: int | None

    @property
    def expires_in(self) -> float | None:
        return None if self.expires_at is None else self.expires_at - time.time()


def decode_flow_token(raw: str, *, now: float | None = None) -> FlowToken:
    """Decode the JWT payload and refuse an expired or malformed token."""
    if not isinstance(raw, str) or raw.count(".") != 2:
        raise UnsolvableError(
            "botdeflector flow token is not a JWT (botDeflectorJwtToken)",
            context={"sample": str(raw)[:24]},
        )
    claims = _jwt_claims(raw)
    expires_at = _int_or_none(claims.get("exp"))
    moment = time.time() if now is None else now
    if expires_at is not None and expires_at <= moment:
        raise ChallengeExpiredError(
            "botdeflector flow token expired; mint a fresh one",
            context={"exp": expires_at, "now": int(moment)},
        )
    steps = tuple(_step_kind(step) for step in _flow_entries(claims))
    return FlowToken(
        raw=raw,
        claims=claims,
        steps=steps,
        session_id=str(claims.get("sessionId") or claims.get("session_id") or ""),
        account_id=str(claims.get("accountId") or claims.get("account_id") or ""),
        site_key=str(claims.get("siteKey") or claims.get("site_key") or ""),
        expires_at=expires_at,
    )


def pow_payload(raw: Any, flow: FlowToken) -> tuple[list[str], pow.ChallengeParams]:
    """Pull the challenges and graph params out of a /pow/get answer."""
    data = _json_dict(raw, what="pow/get")
    challenges = data.get("challenges")
    if not isinstance(challenges, list) or not challenges:
        raise SensorError(
            "botdeflector pow/get returned no challenges",
            context={"keys": sorted(data)[:12]},
        )
    session_id = str(data.get("sessionId") or data.get("session_id") or flow.session_id)
    params = pow.ChallengeParams.from_payload(data.get("params"), session_id=session_id)
    return [str(challenge) for challenge in challenges], params


def solution_body(solutions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"solution": solutions}


def step_token(raw: Any, *, what: str) -> str:
    """A step or flow result token, from JSON or a bare string body."""
    data = _body(raw)
    if isinstance(data, dict):
        for key in ("token", "solutionToken", "solution_token", "challengeFlowResult", "result"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return _checked(value, what=what)
        raise SensorError(
            f"botdeflector {what} returned no token",
            context={"keys": sorted(data)[:12]},
        )
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("{"):
            try:
                return step_token(json.loads(text), what=what)
            except json.JSONDecodeError:
                pass
        if text:
            return _checked(text, what=what)
    raise SensorError(
        f"botdeflector {what} returned an empty body",
        context={"got": type(raw).__name__},
    )


def _body(raw: Any) -> Any:
    """Accept a transport Response, bytes, str or an already-decoded dict."""
    if isinstance(raw, (str, dict)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace")
    body = getattr(raw, "body", None)
    if body is None:
        raise SensorError(
            "botdeflector response is not readable",
            context={"got": type(raw).__name__},
        )
    return bytes(body).decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else body


def _checked(token: str, *, what: str) -> str:
    value = token.strip()
    if len(value) < 16:
        raise SensorError(
            f"botdeflector {what} token is too short",
            context={"length": len(value)},
        )
    if any(char.isspace() for char in value) or "<" in value or ">" in value:
        raise SensorError(
            f"botdeflector {what} returned a page, not a token",
            context={"length": len(value)},
        )
    return value


def _jwt_claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    padded = payload.replace("-", "+").replace("_", "/")
    padded += "=" * ((4 - len(padded) % 4) % 4)
    try:
        blob = base64.b64decode(padded)
        claims = json.loads(blob)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise UnsolvableError(
            "botdeflector flow token payload is not JSON",
            context={"sample": payload[:32]},
        ) from exc
    if not isinstance(claims, dict):
        raise UnsolvableError(
            "botdeflector flow token payload is not an object",
            context={"got": type(claims).__name__},
        )
    return claims


def _flow_entries(claims: dict[str, Any]) -> list[Any]:
    raw = claims.get("challengeFlow") or claims.get("challenge_flow") or []
    if not isinstance(raw, list):
        raise UnsolvableError(
            "botdeflector challengeFlow is not a list",
            context={"got": type(raw).__name__},
        )
    if len(raw) > MAX_FLOW_STEPS:
        raise UnsolvableError(
            "botdeflector challengeFlow is implausibly long",
            context={"steps": len(raw)},
        )
    return raw


def _step_kind(step: Any) -> str:
    value = (step.get("type") or step.get("kind") or step.get("name")) if isinstance(step, dict) else step
    return str(value or "").strip().lower()


def _json_dict(raw: Any, *, what: str) -> dict[str, Any]:
    data = _body(raw)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise SensorError(
                f"botdeflector {what} returned non-JSON",
                context={"sample": data[:120]},
            ) from exc
    if not isinstance(data, dict):
        raise SensorError(
            f"botdeflector {what} returned an unexpected type",
            context={"got": type(data).__name__},
        )
    return data


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
