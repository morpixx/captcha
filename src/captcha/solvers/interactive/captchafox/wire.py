"""CaptchaFox wire: the encoded body, the proof, and the challenge/verify shape.

The widget (2026-11 `api.js`) talks to `api.captchafox.com` with bodies that are
JSON -> gzip -> `0x01 0x04` header + per-byte XOR with `(index + 4)`. The
challenge reply is one of:

  * `{solved: true, token}` — attest/one-click passed with no interaction;
  * `{failed: true}`        — the session is dead;
  * `{type: slide|select|attest|audio, challenge, j, ttl}` — an answer is needed.

`answer` shapes are taken from the widget's own challenge scripts, not guessed:
slide is `{"kind": "position", "position": x}` (x in canvas px, clamp 0..250);
select is `{"kind": "taps", "taps": [{"x", "y"}, ...]}` in challenge-image px.

The missing half of a pure-HTTP solve is `cs` (and optional `k`): they come
from `window.__cf_wapi`, a worker/WASM proof a plain HTTP client cannot
fabricate. Per docs/protocol.md "payload mode" the client may send them with
the task; without them the solver escalates instead of pretending.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from captcha.domain import Task
from captcha.domain.errors import CaptchaError, ChallengeExpiredError

from . import endpoints as ep

_HEADER = b"\x01\x04"


@dataclass(frozen=True)
class Proof:
    """The widget's worker proof: required for any challenge/verify call."""

    cs: str
    k: str | None = None


@dataclass(frozen=True)
class FoxChallenge:
    kind: str
    challenge: dict[str, Any]
    j: Any
    ttl: float | None
    token: str | None
    failed: bool


def encode_body(obj: dict[str, Any]) -> bytes:
    """JSON -> gzip -> header + XOR, exactly as `cq()` in the widget bundle."""
    raw = gzip.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"), mtime=0)
    return _HEADER + bytes(value ^ (index + 4) for index, value in enumerate(raw))


def decode_body(body: bytes) -> dict[str, Any]:
    """Inverse of :func:`encode_body`; used by the tests and by debugging dumps."""
    if not body.startswith(_HEADER):
        raise ValueError("captchafox: body does not start with the 0x01 0x04 header")
    raw = bytes(value ^ (index + 4) for index, value in enumerate(body[2:]))
    decoded = json.loads(gzip.decompress(raw))
    if not isinstance(decoded, dict):
        raise ValueError("captchafox: body is not a JSON object")
    return decoded


def proof_from_task(task: Task) -> Proof | None:
    """Client-collected widget proof: `{"proof": {cs, k}}` or flat `cs`/`k`."""
    payload = task.payload or {}
    proof = payload.get("proof")
    if isinstance(proof, dict):
        cs = proof.get("cs")
        k = proof.get("k")
    else:
        cs = payload.get("cs")
        k = payload.get("k")
    if not isinstance(cs, str) or not cs.strip():
        return None
    return Proof(cs=cs.strip(), k=str(k).strip() if k is not None and str(k).strip() else None)


def site_origin(site_url: str) -> str:
    parsed = urlparse(site_url)
    if not parsed.scheme or not parsed.netloc:
        raise CaptchaError("captchafox: websiteURL is not absolute", context={"url_len": len(site_url)})
    path = parsed.path or "/"
    return parsed.scheme + "://" + parsed.netloc + path


def host_of(site_url: str) -> str:
    return (urlparse(site_url).hostname or "").lower()


def config_url(sitekey: str, site_url: str) -> str:
    return ep.CONFIG.format(sitekey=sitekey) + "?site=" + site_origin(site_url)


def challenge_url(sitekey: str) -> str:
    return ep.CHALLENGE.format(sitekey=sitekey)


def headers() -> dict[str, str]:
    return {"X-Pulse": ep.X_PULSE, "X-Ember": ep.X_EMBER}


def challenge_body(
    config: dict[str, Any],
    proof: Proof,
    *,
    host: str,
    language: str,
    kind: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "lng": language,
        "h": config.get("h") or "",
        "cs": proof.cs,
        "host": host,
    }
    if proof.k:
        body["k"] = {"v": 2, "proof": proof.k}
    if kind:
        body["type"] = kind
    return body


def verify_body(
    sitekey: str,
    *,
    answer: dict[str, Any],
    proof: Proof,
    config: dict[str, Any],
    host: str,
    kind: str,
    trail: list[Any] | None = None,
    elapsed: float = 0.0,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "sk": sitekey,
        "mv": trail or [],
        "t": elapsed,
        "h": config.get("h") or "",
        "cs": proof.cs,
        "type": kind,
        "host": host,
    }
    body.update(answer)
    if proof.k:
        body["k"] = {"v": 2, "proof": proof.k}
    return body


def json_object(reply: Any, what: str) -> dict[str, Any]:
    status = getattr(reply, "status", None)
    if status is not None and int(status) != 200:
        raise ChallengeExpiredError(f"captchafox {what}: HTTP {status}", context={"status": status})
    try:
        data = reply.json()
    except Exception as exc:
        raise ChallengeExpiredError(f"captchafox {what} returned non-JSON", context={"status": status}) from exc
    if not isinstance(data, dict):
        raise ChallengeExpiredError(f"captchafox {what}: reply is not an object", context={"status": status})
    return data


def parse_config(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("error"):
        raise ChallengeExpiredError(
            "captchafox config rejected the site",
            context={"error": str(data.get("error"))[:120]},
        )
    return data


def parse_challenge(data: dict[str, Any]) -> FoxChallenge:
    if data.get("failed"):
        raise ChallengeExpiredError("captchafox: the challenge session failed", context={})
    token = data.get("token")
    if data.get("solved") and isinstance(token, str) and token:
        return FoxChallenge(kind="attest", challenge={}, j=None, ttl=data.get("ttl"), token=token, failed=False)
    kind = str(data.get("type") or "").lower()
    challenge = data.get("challenge")
    if kind == "audio":
        raise CaptchaError(
            "captchafox: audio challenge is out of scope for this solver",
            context={"keys": sorted(data.keys())[:20]},
        )
    if kind in ep.KINDS and isinstance(challenge, dict):
        return FoxChallenge(
            kind=kind,
            challenge=challenge,
            j=data.get("j"),
            ttl=data.get("ttl"),
            token=None,
            failed=False,
        )
    raise ChallengeExpiredError(
        "captchafox: challenge reply has no usable type",
        context={"keys": sorted(data.keys())[:20], "type": kind},
    )


def parse_verified(data: dict[str, Any]) -> str:
    token = data.get("token")
    if data.get("solved") and isinstance(token, str) and token:
        return token
    if data.get("failed"):
        raise ChallengeExpiredError("captchafox: verifier failed the session", context={})
    raise ChallengeExpiredError(
        "captchafox: verifier asked for another challenge instead of a token",
        context={"next_type": str(data.get("type") or "")[:20]},
    )


def answer_from_cv(kind: str, cv: Any, challenge: dict[str, Any]) -> dict[str, Any]:
    """Map the CV primitive's geometry onto the challenge script's answer shape."""
    if kind == "attest":
        return {}
    if kind == "slide":
        offset = float(getattr(cv, "offset", 0.0) or 0.0)
        width = _num(challenge.get("w") or challenge.get("width"))
        if width:
            offset = offset * 250.0 / width
        return {"kind": "position", "position": max(0.0, min(250.0, offset))}
    if kind == "select":
        width = _num(challenge.get("w") or challenge.get("width"))
        height = _num(challenge.get("h") or challenge.get("height"))
        taps = []
        for x, y in getattr(cv, "coords", None) or []:
            taps.append({"x": _clamp(float(x), width), "y": _clamp(float(y), height)})
        if not taps:
            raise ChallengeExpiredError("captchafox: the click solver returned no points", context={})
        return {"kind": "taps", "taps": taps}
    raise CaptchaError("captchafox: unsupported challenge kind for CV", context={"kind": kind})


def image_bytes(value: Any) -> bytes | None:
    """Challenge asset -> bytes when it is inline base64; URLs return None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("data:"):
        text = text.partition(",")[2]
    if text.startswith(("http://", "https://")):
        return None
    try:
        return base64.b64decode(text + "=" * ((-len(text)) % 4))
    except (binascii.Error, ValueError):
        return None


def asset_url(value: Any) -> str | None:
    if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
        return value.strip()
    return None


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, bound: float) -> float:
    if bound <= 0:
        return value
    return max(0.0, min(bound, value))
