"""Basilisk wire helpers: challenge/verify parsing and click answer.

Basilisk (FaucetPay's ad-funded captcha) asks the user to click the sponsor
logo inside a grid and returns a 32-hex captcha_response, which the host page
submits together with the UA that solved it. The 2captcha shape is nested:

    solution.data.captcha_response + solution.headers["User-Agent"]

so this module keeps the response token and the UA separate instead of
flattening them.
"""

from __future__ import annotations

import base64
from typing import Any

import numpy as np

from captcha.behavior import bezier_path, humanize_timing
from captcha.domain import SolverResult
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, UnsolvableError

EXPIRED_STATUSES = frozenset({401, 403, 409, 410, 428, 429})

_EXPIRED_HINTS = ("expire", "stale", "timed out", "timeout", "consumed", "not found", "already solved")


def raise_for_status(resp: Any, what: str) -> None:
    status = int(getattr(resp, "status", 0) or 0)
    if status in EXPIRED_STATUSES:
        raise ChallengeExpiredError(f"{what} rejected the challenge", context={"status": status})
    if status >= 400:
        raise CaptchaError(f"{what} failed", context={"status": status})


def json_object(resp: Any, what: str) -> dict[str, Any]:
    raise_for_status(resp, what)
    try:
        data = resp.json()
    except Exception as exc:
        raise CaptchaError(f"{what} is not JSON", context={"status": getattr(resp, "status", None)}) from exc
    if not isinstance(data, dict):
        raise CaptchaError(f"{what} is not a JSON object", context={"type": type(data).__name__})
    return data


def failure(root: dict[str, Any]) -> tuple[str, str] | None:
    if root.get("success") is True:
        return None
    message = str(_first(root, "message", "msg", "error_description") or "")
    code = _first(root, "error", "error_code", "errorCode", "code")
    if isinstance(code, dict):
        message = str(code.get("message") or message)
        code = code.get("code")
    if code in (None, "", 0, "0", 200, "200", "ok", "OK"):
        return None
    return str(code), message


def raise_for_failure(root: dict[str, Any], what: str) -> None:
    found = failure(root)
    if found is None:
        return
    code, message = found
    if looks_expired(message):
        raise ChallengeExpiredError(f"{what} rejected the challenge", context={"code": code, "message": message[:160]})
    raise UnsolvableError(f"{what} refused the request", context={"code": code, "message": message[:160]})


def looks_expired(message: str) -> bool:
    lowered = (message or "").lower()
    return any(hint in lowered for hint in _EXPIRED_HINTS)


def parse_challenge(root: dict[str, Any]) -> dict[str, Any]:
    """Normalise /v1/challenge: id, image, prompt and optional piece."""
    data = _unwrap(root)
    challenge_id = _str(_first(data, "challengeId", "challenge_id", "id", "cid"))
    image = _str(_first(data, "image", "imageUrl", "image_url", "bg", "background"))
    piece = _str(_first(data, "piece", "pieceUrl", "piece_url", "front"))
    target = _str(_first(data, "target_text", "target", "targetText", "prompt", "question"))
    variant = _str(_first(data, "type", "kind", "variant"))
    if not challenge_id:
        raise UnsolvableError("basilisk challenge has no id", context={"keys": sorted(data)})
    if not image:
        raise UnsolvableError("basilisk challenge has no image", context={"keys": sorted(data)})
    if not target:
        raise UnsolvableError(
            "basilisk challenge has no target text: the click picker has nothing to look for",
            context={"keys": sorted(data)},
        )
    return {
        "challenge_id": challenge_id,
        "image": image,
        "piece": piece,
        "target": target,
        "variant": variant,
    }


def answer_body(challenge: dict[str, Any], cv: SolverResult, seed: Any = None) -> dict[str, Any]:
    """Verify body: click coordinates plus the pointer trace and dwell time.

    FILL: the field names. The baseline used coords/track; keep both raw and
    vendor-shaped keys until one capture confirms which the server reads.
    """
    coords = cv.coords or []
    if not coords:
        raise UnsolvableError("basilisk click solve returned no coordinates", context={"kind": "click"})
    points = [[round(float(x), 1), round(float(y), 1)] for x, y in coords]
    track = build_track(points[0], cv, seed)
    return {
        "challengeId": challenge["challenge_id"],
        "coords": points,
        "track": track,
        "dwell": int(track[-1][2]) if track else 0,
    }


def build_track(target: tuple[float, float], cv: SolverResult, seed: Any = None) -> list[list[float]]:
    raw = (cv.extra or {}).get("trajectory") if cv.extra else None
    if isinstance(raw, list) and raw:
        rows = [
            [float(item[0]), float(item[1]), int(item[2])]
            for item in raw
            if isinstance(item, (list, tuple)) and len(item) == 3
        ]
        if rows:
            return rows
    rng = np.random.default_rng(int(seed)) if seed not in (None, "") else None
    points = bezier_path((0.0, 0.0), target, n_points=18, rng=rng)
    stamps = humanize_timing(len(points), rng=rng)
    return [[round(float(x), 1), round(float(y), 1), int(t)] for (x, y), t in zip(points, stamps, strict=True)]


def find_response(root: dict[str, Any]) -> str:
    """The 32-hex captcha_response the page submits."""
    data = _unwrap(root)
    return _str(_first(data, "captcha_response", "captchaResponse", "token", "response", "answer"))


def b64(body: bytes) -> str:
    return base64.b64encode(body).decode()


def _unwrap(root: dict[str, Any]) -> dict[str, Any]:
    data = root
    for _ in range(3):
        nested = None
        for key in ("data", "result"):
            value = data.get(key)
            if isinstance(value, dict):
                nested = value
                break
        if nested is None:
            break
        data = nested
    return data


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _str(value: Any) -> str:
    return "" if value is None else str(value)
