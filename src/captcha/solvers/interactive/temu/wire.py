"""Temu wire helpers: challenge parsing, pointer trace, session cookies.

Temu answers with a session artefact, not a portable token: a successful
verify returns/refreshes cookies for the page (the anti-content session) and
may include a pass token. The solver hands both back; the catalog projects
this type as a cookie engine, so cookies are the primary field.

Everything here is defensive by design: the endpoints are undocumented and
rotate challenge variants. Unknown shapes raise with the key list instead of
being interpreted as success.
"""

from __future__ import annotations

import base64
from typing import Any

import numpy as np

from captcha.behavior import bezier_path, humanize_timing
from captcha.domain import SolverResult
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, UnsolvableError

EXPIRED_STATUSES = frozenset({401, 403, 409, 410, 428, 429})

_EXPIRED_HINTS = ("expire", "stale", "timed out", "timeout", "not found", "consumed", "invalid captcha")

_SLIDER_HINTS = ("slide", "slider", "puzzle", "arc", "drag")
_CLICK_HINTS = ("click", "semantic", "select", "point")
_GRID_HINTS = ("grid", "3x3", "three", "matrix")


def anti_content(payload: dict[str, Any]) -> str:
    """The client-captured anti_content header, under any of its names."""
    for key in ("anti_content", "anti-content", "antiContent", "a_c"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


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
    """PDD envelopes use success/error_code; some builds only send code."""
    if root.get("success") is True:
        return None
    code = _first(root, "error_code", "errorCode", "code", "errno")
    if code in (None, "", 0, "0", 200, "200", "success", "SUCCESS"):
        return None
    message = str(_first(root, "error_msg", "errorMessage", "message", "msg") or "")
    return str(code), message


def raise_for_failure(root: dict[str, Any], what: str) -> None:
    found = failure(root)
    if found is None:
        return
    code, message = found
    if looks_expired(message):
        raise ChallengeExpiredError(
            f"{what} rejected the challenge",
            context={"code": code, "message": message[:160]},
        )
    raise UnsolvableError(f"{what} refused the request", context={"code": code, "message": message[:160]})


def looks_expired(message: str) -> bool:
    lowered = (message or "").lower()
    return any(hint in lowered for hint in _EXPIRED_HINTS)


def parse_challenge(root: dict[str, Any]) -> dict[str, Any]:
    """Normalise init into captcha id, images, variant and prompt."""
    data = _unwrap(root)
    captcha_id = _str(_first(data, "captchaId", "captcha_id", "id", "challengeId"))
    bg = _str(_first(data, "bgUrl", "bg_url", "bg", "background", "imageUrl"))
    piece = _str(_first(data, "pieceUrl", "piece_url", "piece", "front", "sliceUrl"))
    variant = _str(_first(data, "challengeType", "challenge_type", "type", "variant"))
    prompt = _str(_first(data, "prompt", "target", "question", "instruction", "tip"))
    session = _str(_first(data, "sessionId", "session_id", "scene", "captchaScene"))

    if not captcha_id:
        raise UnsolvableError("temu init has no captcha id", context={"keys": sorted(data)})
    if not bg:
        raise UnsolvableError("temu init has no background image", context={"keys": sorted(data)})
    return {
        "captcha_id": captcha_id,
        "bg": bg,
        "piece": piece,
        "variant": variant,
        "prompt": prompt,
        "session": session,
    }


def variant_kind(variant: str, has_piece: bool, prompt: str) -> str:
    """Map the vendor variant string onto the CV primitive we can drive."""
    lowered = (variant or "").lower()
    if any(hint in lowered for hint in _GRID_HINTS):
        return "grid"
    if any(hint in lowered for hint in _CLICK_HINTS):
        return "click"
    if any(hint in lowered for hint in _SLIDER_HINTS):
        return "slider"
    if prompt and not has_piece:
        return "click"
    return "slider"


def answer_body(
    challenge: dict[str, Any],
    cv: SolverResult,
    kind: str,
    seed: Any = None,
) -> dict[str, Any]:
    """Body of the verify POST: geometry plus the pointer trace."""
    body: dict[str, Any] = {
        "captchaId": challenge["captcha_id"],
        "challengeType": challenge["variant"] or kind,
    }
    if challenge["session"]:
        body["sessionId"] = challenge["session"]
    if kind == "slider":
        offset = float(cv.offset or 0.0)
        if offset <= 0:
            raise UnsolvableError("temu slider resolved to a non-positive offset", context={"offset": offset})
        body["offset"] = int(offset)
        body["track"] = build_track((offset, 4.0), cv, seed)
    else:
        coords = cv.coords or []
        if not coords:
            raise UnsolvableError("temu click solve returned no coordinates", context={"kind": kind})
        body["coords"] = [[round(float(x), 1), round(float(y), 1)] for x, y in coords]
        body["track"] = build_track((float(coords[0][0]), float(coords[0][1])), cv, seed)
    return body


def build_track(target: tuple[float, float], cv: SolverResult, seed: Any = None) -> list[list[float]]:
    """Humanised pointer trace as [x, y, t] rows; CV trajectories win."""
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
    points = bezier_path((0.0, 0.0), target, n_points=22, rng=rng)
    stamps = humanize_timing(len(points), rng=rng)
    return [[round(float(x), 1), round(float(y), 1), int(t)] for (x, y), t in zip(points, stamps, strict=True)]


def extract_cookies(resp: Any, root: dict[str, Any]) -> dict[str, str]:
    """Session cookies from Set-Cookie plus any cookie map in the body."""
    cookies: dict[str, str] = {}
    headers: list[tuple[str, str]] = list(getattr(resp, "headers", []) or [])
    for name, value in headers:
        if str(name).lower() != "set-cookie":
            continue
        pair = str(value).split(";", 1)[0]
        key, sep, val = pair.partition("=")
        if sep and key.strip():
            cookies[key.strip()] = val.strip()
    data = _unwrap(root)
    embedded = _first(data, "cookies", "cookie")
    if isinstance(embedded, dict):
        cookies.update({str(k): str(v) for k, v in embedded.items() if v is not None})
    elif isinstance(embedded, list):
        for item in embedded:
            if isinstance(item, dict) and item.get("name"):
                cookies[str(item["name"])] = str(item.get("value") or "")
    return cookies


def find_token(root: dict[str, Any]) -> str:
    data = _unwrap(root)
    return _str(_first(data, "token", "passToken", "antiContent", "ticket"))


def b64(body: bytes) -> str:
    return base64.b64encode(body).decode()


def _unwrap(root: dict[str, Any]) -> dict[str, Any]:
    """Follow data/result nesting until the payload object is reached."""
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
