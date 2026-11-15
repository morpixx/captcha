"""atbCaptcha wire codecs — challenge parse, sealed answer, check translation.

The sealed `data` field is the whole answer: geometry plus a runtime tag, AES-GCM
under a key the page derives. This module does not implement that cipher; it
asks the worker-wired sealer and refuses to post plaintext that the vendor would
reject as if the geometry were wrong.
"""

from __future__ import annotations

import json
import time
from typing import Any

from captcha.domain import SolverResult
from captcha.domain.enums import CaptchaType
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, UnsolvableError

from . import endpoints as ep


def parse_reply(text: str) -> dict[str, Any]:
    body = (text or "").strip()
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise CaptchaError("atbcaptcha reply is not JSON", context={"head": body[:160]}) from exc
    if not isinstance(data, dict):
        raise CaptchaError("atbcaptcha reply is not a JSON object", context={"type": type(data).__name__})
    return data


def parse_challenge(data: dict[str, Any]) -> dict[str, Any]:
    """Fold the `get` reply into `{token, pic, block}`; token+image are required."""
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    token = _first(root, "token", "captchaToken", "id")
    pic = _first(root, "pic", "image", "background", "bg")
    block = _first(root, "blockPic", "block", "piece", "puzzle")
    if not (token and pic):
        raise UnsolvableError(
            "atbcaptcha get is missing token/pic",
            context={"keys": sorted(root), "code": data.get("code"), "msg": data.get("msg")},
        )
    kind = CaptchaType.SLIDER if block else CaptchaType.CLICK
    return {"token": str(token), "pic": str(pic), "block": str(block) if block else None, "kind": kind}


def answer_plaintext(cv: SolverResult, kind: CaptchaType, *, started: float) -> dict[str, Any]:
    """The geometry the SDK seals: end x for a slider, ordered points for a click."""
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if kind is CaptchaType.SLIDER:
        return {"x": round(cv.offset or 0), "t": elapsed_ms}
    points = cv.coords or []
    if not points:
        raise UnsolvableError("atbcaptcha click task: CV delegate returned no points", context={})
    return {"pts": [[round(x), round(y)] for x, y in points], "t": elapsed_ms}


def check_body(captcha_id: str, token: str, sealed: Any, *, type_: int) -> dict[str, Any]:
    """Build the `check` body around the sealed answer."""
    if not isinstance(sealed, str) or not sealed:
        raise UnsolvableError("atbcaptcha sealer returned an empty data field", context={})
    return {"captchaId": captcha_id, "token": token, "data": sealed, "type": int(type_), "ts": int(time.time() * 1000)}


def parse_check(data: dict[str, Any]) -> str:
    """Return `captchaVerification`, or raise the mapped domain error."""
    root = data.get("data") if isinstance(data.get("data"), dict) else {}
    verification = _first(root, "captchaVerification", "verification", "token")
    if data.get("success") is True and verification:
        return str(verification)
    message = str(data.get("msg") or data.get("message") or root.get("msg") or "")
    code = str(data.get("code") or "")
    if _expired(message) or _expired(code):
        raise ChallengeExpiredError(
            "atbcaptcha check: the round expired before the answer landed",
            context={"code": code, "msg": message},
        )
    if verification:
        raise CaptchaError(
            "atbcaptcha check returned a verification with success=false",
            context={"code": code, "msg": message},
        )
    raise CaptchaError(
        "atbcaptcha check returned no captchaVerification",
        context={"keys": sorted(data), "code": code, "msg": message},
    )


def _expired(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in ep.EXPIRED_MARKERS)


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
