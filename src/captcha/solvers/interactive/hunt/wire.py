"""Hunt wire helpers: challenge parsing, trace, token extraction.

Hunt is distributed as an in-page SDK (hd-api/.../api.js) plus a server API.
The server API mode takes a per-session JWT and answers a challenge object;
the widget mode runs the SDK in the page and hands back a token. This module
covers the server-API half; the widget half lives in browser.py because it
needs a real page to execute the SDK.

The integration points vary by site (host and prompt), so the parser keeps
the aliases we have seen and fails with the key list when the shape moves.
"""

from __future__ import annotations

import base64
from typing import Any

import numpy as np

from captcha.behavior import bezier_path, humanize_timing
from captcha.domain import SolverResult
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, UnsolvableError

EXPIRED_STATUSES = frozenset({401, 403, 409, 410, 428, 429})

_EXPIRED_HINTS = ("expire", "stale", "timed out", "timeout", "consumed", "not found", "invalid token")

_SLIDER_HINTS = ("slide", "slider", "puzzle")
_CLICK_HINTS = ("click", "point", "select", "semantic")


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
    code = _first(root, "error_code", "errorCode", "code", "error")
    if code in (None, "", 0, "0", 200, "200", "ok", "OK"):
        return None
    message = str(_first(root, "error_msg", "message", "msg", "description") or "")
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
    """Normalise init: image, prompt, challenge id, variant and piece."""
    data = _unwrap(root)
    image = _str(_first(data, "imageUrl", "image", "bg", "background", "asset"))
    piece = _str(_first(data, "pieceUrl", "piece", "slice"))
    prompt = _str(_first(data, "prompt", "target", "question", "instruction", "task"))
    challenge_id = _str(_first(data, "huntId", "hunt_id", "challengeId", "id"))
    variant = _str(_first(data, "type", "challengeType", "variant", "kind"))
    images = _first(data, "images", "assets")
    if not image and isinstance(images, list) and images:
        image = _str(images[0])
    if not image:
        raise UnsolvableError("hunt init has no challenge image", context={"keys": sorted(data)})
    if not challenge_id:
        raise UnsolvableError("hunt init has no challenge id", context={"keys": sorted(data)})
    return {
        "image": image,
        "piece": piece,
        "prompt": prompt,
        "challenge_id": challenge_id,
        "variant": variant,
    }


def variant_kind(variant: str, has_piece: bool, prompt: str) -> str:
    lowered = (variant or "").lower()
    if any(hint in lowered for hint in _SLIDER_HINTS):
        return "slider"
    if any(hint in lowered for hint in _CLICK_HINTS):
        return "click"
    if has_piece and not prompt:
        return "slider"
    return "click"


def answer_body(
    challenge: dict[str, Any],
    cv: SolverResult,
    kind: str,
    seed: Any = None,
) -> dict[str, Any]:
    if kind == "slider":
        offset = float(cv.offset or 0.0)
        if offset <= 0:
            raise UnsolvableError("hunt slider resolved to a non-positive offset", context={"offset": offset})
        return {
            "huntId": challenge["challenge_id"],
            "type": challenge["variant"] or kind,
            "offset": int(offset),
            "track": build_track((offset, 4.0), cv, seed),
        }
    coords = cv.coords or []
    if not coords:
        raise UnsolvableError("hunt click solve returned no coordinates", context={"kind": kind})
    return {
        "huntId": challenge["challenge_id"],
        "type": challenge["variant"] or kind,
        "coords": [[round(float(x), 1), round(float(y), 1)] for x, y in coords],
        "track": build_track((float(coords[0][0]), float(coords[0][1])), cv, seed),
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
    points = bezier_path((0.0, 0.0), target, n_points=20, rng=rng)
    stamps = humanize_timing(len(points), rng=rng)
    return [[round(float(x), 1), round(float(y), 1), int(t)] for (x, y), t in zip(points, stamps, strict=True)]


def find_token(root: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return (token, data block) from a verify reply.

    The 2captcha shape for Hunt is nested: solution.data.token. Keep a whole
    data block when the reply carries one so extra vendor fields survive.
    """
    data = _unwrap(root)
    value = _first(data, "token", "response")
    if not value:
        value = _first(root, "token", "response")
    token = value if isinstance(value, str) else ""
    return token, data


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
