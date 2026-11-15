"""Aliyun NoCaptcha wire helpers: JSONP framing, envelope, answer geometry.

NoCaptcha v1 speaks JSONP: get.jsonp/analyze.jsonp wrap the payload in a
callback. The envelope carries a numeric result code (100 = success) and the
interesting object under result.data. This module keeps the unwrapping, the
code classification and the answer encoding out of the solver.

The modern Captcha 2.0 widget does not use these endpoints at all - it signs a
CaptchaVerifyParam in the page - so that half lives in browser.py.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import numpy as np

from captcha.behavior import bezier_path, humanize_timing
from captcha.domain import SolverResult
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, UnsolvableError

EXPIRED_STATUSES = frozenset({400, 401, 403, 404, 408, 410, 429})

#: Result codes that mean the challenge is gone. 100 is success; everything
#: else is either stale (retry with a fresh one) or a shape we cannot drive.
_EXPIRED_CODES = frozenset({"400", "401", "402", "403", "404", "408", "410", "419", "429"})

_EXPIRED_HINTS = ("expire", "stale", "timed out", "timeout", "not found", "consumed", "session")

_SUCCESS_CODES = frozenset({"100", "0", "200", "success", "SUCCESS"})


def jsonp_object(text: str, what: str) -> dict[str, Any]:
    """Decode a JSONP body (or a bare JSON object) into a dict."""
    raw = (text or "").strip().lstrip("﻿")
    if not raw:
        raise CaptchaError(f"{what} returned an empty body", context={})
    if not raw.startswith(("{", "[")):
        start = raw.find("(")
        end = raw.rfind(")")
        if start == -1 or end <= start:
            raise CaptchaError(f"{what} is not JSONP", context={"head": raw[:120]})
        raw = raw[start + 1 : end].strip()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise CaptchaError(f"{what} is not JSON", context={"head": raw[:120]}) from exc
    if not isinstance(data, dict):
        raise CaptchaError(f"{what} is not a JSON object", context={"type": type(data).__name__})
    return data


def read_envelope(root: dict[str, Any], what: str) -> dict[str, Any]:
    """Validate result.code and return result.data (or the result object)."""
    result = root.get("result") if isinstance(root.get("result"), dict) else root
    code = _first(result, "code", "status")
    data = result.get("data")
    success = root.get("success")

    if success is True or code is None:
        return data if data is not None else result

    text = str(code)
    if text in _SUCCESS_CODES:
        return data if data is not None else result
    message = str(_first(result, "msg", "message", "error") or root.get("message") or "")
    if text in _EXPIRED_CODES or looks_expired(message):
        raise ChallengeExpiredError(
            f"{what} rejected the challenge",
            context={"code": text, "message": message[:160]},
        )
    raise UnsolvableError(
        f"{what} refused the request",
        context={"code": text, "message": message[:160]},
    )


def raise_for_status(resp: Any, what: str) -> None:
    status = int(getattr(resp, "status", 0) or 0)
    if status in EXPIRED_STATUSES:
        raise ChallengeExpiredError(f"{what} rejected the challenge", context={"status": status})
    if status >= 400:
        raise CaptchaError(f"{what} failed", context={"status": status})


def parse_challenge(root: dict[str, Any]) -> dict[str, Any]:
    """Normalise get.jsonp into session, sig, token, images and variant."""
    data = _as_dict(read_envelope(root, "alibaba get"))
    session = _str(_first(data, "csessionid", "csessionId", "sessionId", "session"))
    sig = _str(_first(data, "sig", "signature"))
    token = _str(_first(data, "token", "nc_token", "ticket"))
    scene = _str(_first(data, "scene", "sceneId", "SceneId"))
    bg = _str(_first(data, "bg", "background", "bgUrl", "backgroundUrl"))
    front = _str(_first(data, "front", "piece", "frontUrl", "slice"))
    variant = _str(_first(data, "type", "kind", "variant"))
    prompt = _str(_first(data, "prompt", "target", "question", "tip"))
    if not session or not sig:
        raise UnsolvableError("alibaba challenge has no csessionid/sig", context={"keys": sorted(data)})
    if not bg:
        raise UnsolvableError("alibaba challenge has no background image", context={"keys": sorted(data)})
    return {
        "session": session,
        "sig": sig,
        "token": token,
        "scene": scene,
        "bg": bg,
        "front": front,
        "variant": variant,
        "prompt": prompt,
    }


def variant_kind(variant: str, has_front: bool, prompt: str) -> str:
    lowered = (variant or "").lower()
    if "rotate" in lowered:
        return "rotate"
    if "click" in lowered or "point" in lowered or "word" in lowered or "text" in lowered:
        return "click"
    if "slide" in lowered or "slider" in lowered or "drag" in lowered:
        return "slider"
    if prompt and not has_front:
        return "click"
    return "slider"


def analyze_body(
    challenge: dict[str, Any],
    cv: SolverResult,
    kind: str,
    *,
    appkey: str,
    ua: str,
    seed: Any = None,
) -> dict[str, Any]:
    """Form body for analyze.jsonp: geometry plus the pointer trace.

    FILL: the exact field names/units. The baseline used answer/track; the
    legacy SDK also sends an encrypted w blob. Capture one analyze request and
    trim this dict instead of guessing further.
    """
    if kind == "slider":
        offset = float(cv.offset or 0.0)
        if offset <= 0:
            raise UnsolvableError("alibaba slider resolved to a non-positive offset", context={"offset": offset})
        geometry: dict[str, Any] = {"x": int(offset)}
        target = (offset, 4.0)
    elif kind == "rotate":
        angle = float(cv.angle or 0.0)
        if angle == 0.0:
            raise UnsolvableError("alibaba rotate solve returned a zero angle", context={})
        geometry = {"angle": round(angle, 2)}
        target = (float(angle), 0.0)
    else:
        coords = cv.coords or []
        if not coords:
            raise UnsolvableError("alibaba click solve returned no coordinates", context={"kind": kind})
        geometry = {"points": [[round(float(x), 1), round(float(y), 1)] for x, y in coords]}
        target = (float(coords[0][0]), float(coords[0][1]))

    return {
        "csessionid": challenge["session"],
        "sig": challenge["sig"],
        "token": challenge["token"],
        "scene": challenge["scene"],
        "appkey": appkey,
        "ua": ua,
        "answer": json.dumps(geometry, separators=(",", ":")),
        "w": json.dumps(build_track(target, cv, seed), separators=(",", ":")),
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
    points = bezier_path((0.0, 0.0), target, n_points=22, rng=rng)
    stamps = humanize_timing(len(points), rng=rng)
    return [[round(float(x), 1), round(float(y), 1), int(t)] for (x, y), t in zip(points, stamps, strict=True)]


def find_result(root: dict[str, Any]) -> dict[str, Any]:
    """The two values the site backend needs (nc_sid/nc_token) or the v2 param."""
    data = _as_dict(read_envelope(root, "alibaba analyze"))
    out: dict[str, Any] = {}
    sid = _first(data, "nc_sid", "ncSid", "sid", "sessionId")
    token = _first(data, "nc_token", "ncToken", "token", "ticket")
    param = _first(data, "CaptchaVerifyParam", "captchaVerifyParam", "captcha_verify_param")
    if sid:
        out["nc_sid"] = str(sid)
    if token:
        out["nc_token"] = str(token)
    if param:
        out["CaptchaVerifyParam"] = str(param)
    if not out:
        raise UnsolvableError(
            "alibaba analyze returned neither nc_sid/nc_token nor CaptchaVerifyParam",
            context={"keys": sorted(data)},
        )
    return out


def looks_expired(message: str) -> bool:
    lowered = (message or "").lower()
    return any(hint in lowered for hint in _EXPIRED_HINTS)


def b64(body: bytes) -> str:
    return base64.b64encode(body).decode()


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise CaptchaError("alibaba payload is not JSON", context={"head": value[:120]}) from exc
        if isinstance(parsed, dict):
            return parsed
    raise CaptchaError("alibaba payload is not a JSON object", context={"type": type(value).__name__})


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _str(value: Any) -> str:
    return "" if value is None else str(value)
