"""CutCaptcha wire codecs — challenge parse, pointer track, verify translation.

The vendor replays the drag before accepting the offset, so the track is not
decoration: it is generated with the same human-shaped curve the slider lane
uses for every other engine (`captcha.behavior`), on a seeded generator so a
test can assert the exact shape.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from captcha.behavior import bezier_path, humanize_timing
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, UnsolvableError

from . import endpoints as ep

#: Where the piece sits vertically; CutCaptcha's slider only moves on x.
PIECE_Y = 0.0


def parse_reply(text: str) -> dict[str, Any]:
    body = (text or "").strip()
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise CaptchaError("cutcaptcha reply is not JSON", context={"head": body[:160]}) from exc
    if not isinstance(data, dict):
        raise CaptchaError("cutcaptcha reply is not a JSON object", context={"type": type(data).__name__})
    return data


def parse_challenge(data: dict[str, Any]) -> dict[str, str]:
    """Fold the challenge reply into `{cid, bg, piece}`; all three are required."""
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    cid = _first(root, "cid", "challengeId", "challenge_id", "id")
    bg = _first(root, "bgUrl", "bg", "background", "bg_url")
    piece = _first(root, "pieceUrl", "piece", "puzzle", "piece_url")
    if not (cid and bg and piece):
        raise UnsolvableError(
            "cutcaptcha challenge is missing cid/bg/piece",
            context={"keys": sorted(root), "error": data.get("error")},
        )
    return {"cid": str(cid), "bg": str(bg), "piece": str(piece)}


def drag_track(offset: float, *, points: int = 24, rng: np.random.Generator | None = None) -> list[dict[str, float]]:
    """Human-shaped drag from the piece's home to `offset`, as `{x, y, t}` samples.

    The curve is a jittered bezier with eased timing — a straight line from 0 to
    `offset` is the shape the track check exists to catch. At least
    `MIN_TRACK_POINTS` samples are emitted regardless of the caller's request.
    """
    count = max(int(points), ep.MIN_TRACK_POINTS)
    path = bezier_path((0.0, PIECE_Y), (float(offset), PIECE_Y), n_points=count, rng=rng)
    timings = humanize_timing(count, rng=rng)
    return [{"x": round(x, 2), "y": round(y, 2), "t": int(t)} for (x, y), t in zip(path, timings, strict=True)]


def parse_verify(data: dict[str, Any]) -> str:
    """Return the token, or raise the mapped domain error."""
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    token = _first(root, "token", "response", "answer")
    failed = data.get("success") is False or data.get("error") not in (None, "", 0, "0", False)
    if token and not failed:
        return str(token)
    message = str(data.get("error") or data.get("message") or root.get("error") or "")
    if _expired(message) or str(data.get("code") or "").lower() in {"expired", "challenge_expired"}:
        raise ChallengeExpiredError(
            "cutcaptcha verify: the challenge expired before the answer landed",
            context={"message": message, "code": data.get("code")},
        )
    if token:
        raise CaptchaError(
            "cutcaptcha verify returned a token with a failure flag",
            context={"success": data.get("success"), "error": data.get("error")},
        )
    raise CaptchaError(
        "cutcaptcha verify returned no token",
        context={"keys": sorted(data), "error": data.get("error")},
    )


def _expired(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in ep.EXPIRED_MARKERS)


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
