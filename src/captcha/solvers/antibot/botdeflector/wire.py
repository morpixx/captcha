"""BotDeflector flow token, PoW challenge and step tokens.

The public wire (2026-10 capture in resolvrlabs/botdeflector-solver, the
botdeflector-pow reference implementation, and the takionapi teardown):

    GET  /pow/get      x-tt-flow-token                -> {"challengeToken": <jwt>}
    POST /pow/verify   {solution, params, solution_fingerprint, time,
                        challengeToken, challengeFlowToken}   -> {"token": <jwt>}
    GET  /verify-flow  x-tt-ch-flow-token,
                       x-tt-solution-tokens (comma joined),
                       x-tt-client-fingerprint        -> {"ChallengeFlowResult": <jwt>}

The challenge JWT payload is {challenges, params, challengeType, sessionId, it,
exp}. The graph seed is the sessionId -- nothing in the payload says so -- and
every step of one flow shares one graph, so build it once. A step token is a JWT
{sessionId, type, exp}.

The flow token (botDeflectorJwtToken) decodes to {accountId, siteKey,
sessionId, challengeFlow, iat, exp}: 120 s on Queue-it, 600 s on
botdeflector.eu. The final ChallengeFlowResult carries solvedTypes ("inv,icon")
and is spent as ?tt_solutions= on a standalone site, or redeemed through
Queue-it's own verify call.

Everything here is deliberately tolerant about key spelling -- camelCase and
snake_case both shipped -- but strict about expiry, because a flow token is
single-use and an expired one must not cost a solve.
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


@dataclass(frozen=True, slots=True)
class PowChallenge:
    """One /pow/get answer: the JWT it was wrapped in plus its decoded payload."""

    token: str
    payload: dict[str, Any]
    challenges: tuple[str, ...]
    params: pow.ChallengeParams
    session_id: str

    @property
    def graph_key(self) -> tuple[int, int, int, str]:
        """Every inv step of a flow shares this key, so the graph is built once."""
        return (self.params.nodes_bits, self.params.edges_bits, self.params.cycle_length, self.params.graph_seed)


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


def pow_challenge(raw: Any, flow: FlowToken, *, now: float | None = None) -> PowChallenge:
    """Decode a /pow/get answer into the challenge JWT and its graph inputs.

    The live answer nests everything inside challengeToken; an unwrapped body is
    accepted too, because that is what the reference PoW CLIs eat.
    """
    data = _json_dict(raw, what="pow/get")
    token = _first_str(data, "challengeToken", "challenge_token")
    payload = _jwt_claims(token) if token else data
    expires_at = _int_or_none(payload.get("exp"))
    moment = time.time() if now is None else now
    if expires_at is not None and expires_at <= moment:
        raise ChallengeExpiredError(
            "botdeflector pow challenge expired between get and verify",
            context={"exp": expires_at, "now": int(moment)},
        )
    challenges = payload.get("challenges")
    if not isinstance(challenges, list) or not challenges:
        raise SensorError(
            "botdeflector pow/get returned no challenges",
            context={"keys": sorted(payload)[:12]},
        )
    session_id = str(payload.get("sessionId") or payload.get("session_id") or flow.session_id)
    raw_params = payload.get("params")
    if not isinstance(raw_params, dict):
        raise SensorError(
            "botdeflector pow/get returned no params blob",
            context={"keys": sorted(payload)[:12]},
        )
    params = pow.ChallengeParams.from_payload(raw_params, session_id=session_id)
    return PowChallenge(
        token=token,
        payload=dict(payload),
        challenges=tuple(str(challenge) for challenge in challenges),
        params=params,
        session_id=session_id,
    )


def pow_payload(raw: Any, flow: FlowToken) -> tuple[list[str], pow.ChallengeParams]:
    """Challenges and graph params of a /pow/get answer (compatibility shape)."""
    challenge = pow_challenge(raw, flow)
    return list(challenge.challenges), challenge.params


def pow_verify_body(
    challenge: PowChallenge,
    solutions: list[dict[str, Any]],
    fingerprint: str,
    *,
    elapsed_s: float,
    flow_token: str,
) -> dict[str, Any]:
    """The /pow/verify body a browser sends, field for field.

    params is the payload params plus the session id as GRAPH_SEED, time is the
    browser's reported solve time in seconds with three decimals, and the two
    JWTs ride in the body rather than in a header.
    """
    params = dict(challenge.payload.get("params") or {})
    params["GRAPH_SEED"] = challenge.params.graph_seed
    body: dict[str, Any] = {
        "solution": solutions,
        "params": params,
        "solution_fingerprint": fingerprint,
        "time": f"{max(elapsed_s, 0.0):.3f}",
        "challengeFlowToken": flow_token,
    }
    if challenge.token:
        body["challengeToken"] = challenge.token
    return body


def flow_result_token(raw: Any) -> str:
    """The ChallengeFlowResult JWT out of /verify-flow."""
    data = _json_dict(raw, what="verify-flow")
    value = _first_str(data, "ChallengeFlowResult", "challengeFlowResult", "challenge_flow_result", "token", "result")
    if not value:
        raise SensorError(
            "botdeflector verify-flow returned no ChallengeFlowResult",
            context={"keys": sorted(data)[:12]},
        )
    return _checked(value, what="verify-flow")


def solution_body(solutions: list[dict[str, Any]]) -> dict[str, Any]:
    return {"solution": solutions}


def step_token(raw: Any, *, what: str) -> str:
    """A step token, from JSON or a bare string body.

    A step token is a JWT, so anything that is not an unbroken, long-ish string
    is a page or an error body -- never a token.
    """
    data = _body(raw)
    if isinstance(data, dict):
        keys = ("token", "solutionToken", "solution_token", "challengeFlowResult", "ChallengeFlowResult", "result")
        for key in keys:
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
    parts = token.split(".")
    if len(parts) != 3:
        raise SensorError(
            "botdeflector challenge token is not a JWT",
            context={"sample": str(token)[:24]},
        )
    payload = parts[1]
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


def _first_str(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
