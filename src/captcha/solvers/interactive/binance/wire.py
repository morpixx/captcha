"""Binance wire helpers: challenge parsing, trajectory, answer envelope.

The answer Binance accepts is not a bare offset. The page encrypts a JSON blob
holding the pointer trace and the final geometry, sends it as "data", and
signs it with "s". This module keeps that encoding in one place so the solver
reads as a flow and the crypto can be tested against a fixed vector.

Key derivation is the one piece the public write-ups do not pin down: the
baseline records key = sig[:16], iv = sig[16:32] for a 32+ byte sig and the
fallback below hashes the pair. A live capture of one solve settles it; until
then both branches are deterministic and the verify call fails loudly instead
of inventing a token.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import numpy as np

from captcha.behavior import bezier_path, humanize_timing
from captcha.crypto import aes_cbc_encrypt, hmac_sha256
from captcha.domain import SolverResult
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, UnsolvableError

from .endpoints import IMAGE_BASE

#: The transport already maps 403/429 to ChallengeExpiredError; these are the
#: statuses a fake/alternate client can surface without going through it.
EXPIRED_STATUSES = frozenset({401, 403, 409, 410, 428, 429})

#: Message fragments that mean "this challenge is no longer the one the server
#: issued" rather than "you solved it wrong". The API has no documented codes,
#: so the human-readable message is the only signal we have (FILL: code set).
_EXPIRED_HINTS = (
    "expire",
    "timed out",
    "timeout",
    "stale",
    "consumed",
    "challenge was",
    "captcha invalid",
)

#: Variants that are not a one-image slider. BOX appears after repeated failures;
#: a fresh session is cheaper than pretending our slider answer fits it.
_NON_SLIDER_TYPES = frozenset({"BOX", "GRID", "ICON", "CLICK", "TEXT"})


def raise_for_status(resp: Any, what: str) -> None:
    """Translate a non-2xx response into the solver error taxonomy."""
    status = int(getattr(resp, "status", 0) or 0)
    if status in EXPIRED_STATUSES:
        raise ChallengeExpiredError(
            f"{what} rejected the challenge",
            context={"status": status},
        )
    if status >= 400:
        raise CaptchaError(f"{what} failed", context={"status": status})


def json_object(resp: Any, what: str) -> dict[str, Any]:
    """Decode a JSON object, mapping transport/status failures first."""
    raise_for_status(resp, what)
    try:
        data = resp.json()
    except Exception as exc:
        raise CaptchaError(f"{what} is not JSON", context={"status": getattr(resp, "status", None)}) from exc
    if not isinstance(data, dict):
        raise CaptchaError(f"{what} is not a JSON object", context={"type": type(data).__name__})
    return data


def failure(root: dict[str, Any]) -> tuple[str, str] | None:
    """Return (code, message) when the envelope is a failure, else None.

    Binance wraps every response in {code, message, data, success}; a missing
    envelope (bare object) counts as success so the parser can still read field
    aliases from older builds.
    """
    success = root.get("success")
    code = root.get("code")
    if success is True:
        return None
    if code is None:
        return None if success is None else (str(code), str(root.get("message") or ""))
    text = str(code)
    if text in ("", "0", "000000", "200"):
        return None
    return text, str(root.get("message") or root.get("msg") or "")


def raise_for_failure(root: dict[str, Any], what: str) -> None:
    """Classify an API-level failure: stale challenge or structurally wrong task."""
    found = failure(root)
    if found is None:
        return
    code, message = found
    if looks_expired(message):
        raise ChallengeExpiredError(
            f"{what} rejected the challenge",
            context={"code": code, "message": message[:160]},
        )
    raise UnsolvableError(
        f"{what} refused the request",
        context={"code": code, "message": message[:160]},
    )


def looks_expired(message: str) -> bool:
    lowered = (message or "").lower()
    return any(hint in lowered for hint in _EXPIRED_HINTS)


def parse_challenge(root: dict[str, Any]) -> dict[str, Any]:
    """Normalise getCaptcha into the fields the solver needs.

    Raises UnsolvableError for a shape we cannot drive (BOX upgrade, missing
    sig/validateId) and for a challenge without a background image.
    """
    data = root.get("data") if isinstance(root.get("data"), dict) else root
    sig = _str(_first(data, "sig", "signature"))
    validate_id = _str(
        _first(data, "validateId", "securityCheckResponseValidateId", "sessionId", "uid")
    )
    captcha_type = _str(_first(data, "captchaType", "captchaTypeName", "type")).upper()
    bg = _str(_first(data, "bgLink", "bg", "background", "bgUrl", "path2"))
    piece = _str(_first(data, "puzzleLink", "piece", "puzzle", "front", "puzzleUrl", "path1"))
    salt = _str(_first(data, "salt", "saltKey"))
    tag = _str(_first(data, "tag"))
    ek = _str(_first(data, "ek"))
    piece_width = _int(_first(data, "pieceWidth", "piece_width", "sliderWidth"))

    if not sig:
        raise UnsolvableError("binance challenge has no sig", context={"keys": sorted(data)})
    if not validate_id:
        raise UnsolvableError(
            "binance challenge has no validateId — run the precheck or pass validateId in the payload",
            context={"keys": sorted(data)},
        )
    if captcha_type in _NON_SLIDER_TYPES:
        raise UnsolvableError(
            f"binance upgraded the challenge to {captcha_type}: a fresh session is required",
            context={"captchaType": captcha_type, "keys": sorted(data)},
        )
    if not bg:
        raise UnsolvableError("binance challenge has no background image", context={"keys": sorted(data)})

    return {
        "sig": sig,
        "validate_id": validate_id,
        "captcha_type": captcha_type or "SLIDER",
        "bg": image_url(bg),
        "piece": image_url(piece) if piece else "",
        "salt": salt,
        "tag": tag,
        "ek": ek,
        "piece_width": piece_width,
    }


def image_url(path: str) -> str:
    """Resolve a challenge path against the static image host."""
    if path.startswith(("http://", "https://", "data:")):
        return path
    return IMAGE_BASE + ("/" if not path.startswith("/") else "") + path


def build_track(cv: SolverResult, seed: Any = None) -> list[list[float]]:
    """Pointer trace as [x, y, t] rows.

    A CV slider already ships a humanised trajectory in "extra"; use it
    verbatim when present so the trace and the offset agree. Otherwise build a
    jittered cubic bezier with non-decreasing millisecond timestamps.
    """
    raw = (cv.extra or {}).get("trajectory") if cv.extra else None
    if isinstance(raw, list) and raw:
        rows: list[list[float]] = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                rows.append([float(item[0]), float(item[1]), int(item[2])])
        if rows:
            return rows

    offset = float(cv.offset or 0.0)
    if offset <= 0:
        raise UnsolvableError("binance slider resolved to a non-positive offset", context={"offset": offset})
    rng = np.random.default_rng(int(seed)) if seed not in (None, "") else None
    points = bezier_path((0.0, 0.0), (offset, 4.0), n_points=26, rng=rng)
    stamps = humanize_timing(len(points), rng=rng)
    return [[round(float(x), 1), round(float(y), 1), int(t)] for (x, y), t in zip(points, stamps, strict=True)]


def encode_answer(
    sig: str,
    salt: str,
    offset: float,
    track: list[list[float]],
    *,
    meta: dict[str, Any] | None = None,
) -> str:
    """Encrypt the answer blob the way the page does, and base64 it.

    FILL: the exact plaintext key set. Public write-ups agree on the fields the
    blob carries (trace + geometry) but not on their names; the server rejects
    unknown shapes with a code, so capture one request body and trim this dict.
    """
    key, iv = derive_keys(sig, salt)
    blob: dict[str, Any] = {"track": track, "offset": int(offset)}
    if meta:
        blob.update({k: v for k, v in meta.items() if v not in (None, "")})
    plain = json.dumps(blob, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.b64encode(aes_cbc_encrypt(key, iv, plain)).decode()


def derive_keys(sig: str, salt: str) -> tuple[bytes, bytes]:
    """AES-CBC key/IV from the challenge sig.

    Baseline note: key = sig[:16], iv = sig[16:32] when sig is long enough.
    FILL: confirm on a live capture; short sigs use the hashed fallback so the
    envelope stays deterministic instead of throwing mid-task.
    """
    raw = sig.encode()
    if len(raw) >= 32:
        return raw[:16], raw[16:32]
    key = hashlib.sha256(raw + b"|key").digest()[:16]
    iv = hashlib.sha256((salt or sig).encode() + b"|iv").digest()[:16]
    return key, iv


def sign_answer(sign_key: str, encoded: str) -> str:
    """HMAC tag for the "s" field. FILL: the page's exact MAC/secret."""
    return hmac_sha256(sign_key.encode(), encoded.encode()).hexdigest()


def token_from(root: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Pull the token and its data object out of the validateCaptcha reply.

    The token is prefixed "captcha#"; the prefix is part of the value clients
    submit, so it is never stripped here.
    """
    data = root.get("data") if isinstance(root.get("data"), dict) else root
    token = _first(data, "token", "validateCode", "captchaToken", "securityToken")
    if not token:
        token = _first(root, "token", "validateCode", "captchaToken")
    return (_str(token), data)


def b64(body: bytes) -> str:
    """CV primitives take base64 strings, not raw bytes."""
    return base64.b64encode(body).decode()


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _str(value: Any) -> str:
    return "" if value is None else str(value)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
