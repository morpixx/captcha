"""Yidun wire codecs — challenge parsing, answer sealing, check translation.

`get` and `check` speak JSONP on some builds (`__wmjsonp(...)`) and JSON on
others, so every reply goes through `parse_reply` before anything reads it.

The `data`/`cb` pair on `check` is the SDK's own encryption of the pointer track
plus an environment tag; both are produced by JS that checks the DOM, canvas and
mouse history. This module therefore does not implement the cipher — it asks the
worker-wired sealer for the envelope and refuses to post a guessed one.
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
    """Decode a Yidun reply: JSON, or JSONP with any callback name."""
    body = _strip_jsonp(text)
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise CaptchaError("yidun reply is not JSON", context={"head": (text or "")[:160]}) from exc
    if not isinstance(data, dict):
        raise CaptchaError("yidun reply is not a JSON object", context={"type": type(data).__name__})
    return data


def parse_challenge(data: dict[str, Any]) -> dict[str, Any]:
    """Fold `get` into `{token, bg, front, kind}`; missing pieces are fatal."""
    res = _first_dict(data.get("data"), data.get("result")) or data
    token = _first(res, "token", "id")
    bg = _first_url(res.get("bg"))
    front = _first_url(res.get("front"))
    if not token:
        raise UnsolvableError(
            "yidun get returned no challenge token",
            context={"keys": sorted(res), "error": data.get("error"), "msg": data.get("msg")},
        )
    if not bg:
        raise UnsolvableError(
            "yidun get returned no background image",
            context={"keys": sorted(res), "type": res.get("type")},
        )
    kind = CaptchaType.SLIDER if front else CaptchaType.CLICK
    return {
        "token": str(token),
        "bg": bg,
        "front": front,
        "kind": kind,
        "zone_id": _first(res, "zoneId", "zone_id") or "",
        "type": res.get("type") or (ep.TYPE_SLIDER if front else ep.TYPE_CLICK),
    }


def answer_plaintext(cv: SolverResult, kind: CaptchaType, *, started: float) -> dict[str, Any]:
    """The answer object the SDK seals: end x plus the pointer track, or the points."""
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if kind is CaptchaType.SLIDER:
        return {"x": round(cv.offset or 0), "t": elapsed_ms, "s": ""}
    points = cv.coords or []
    if not points:
        raise UnsolvableError("yidun click task: CV delegate returned no points", context={})
    return {"p": [[round(x), round(y)] for x, y in points], "t": elapsed_ms}


def check_body(challenge: dict[str, Any], sealed: dict[str, Any], *, captcha_id: str) -> dict[str, Any]:
    """Build the `check` body. Both sealed halves are required by the vendor."""
    plain = str(sealed.get("data") or "")
    cb = str(sealed.get("cb") or "")
    if not plain or not cb:
        raise UnsolvableError(
            "yidun sealer returned an incomplete envelope",
            context={"has_data": bool(plain), "has_cb": bool(cb)},
        )
    return {
        "id": captcha_id,
        "token": challenge["token"],
        "data": plain,
        "cb": cb,
        "width": int(challenge.get("width") or 320),
        "extraData": "",
        "runEnv": ep.RUN_ENV_WEB,
        "type": int(challenge.get("type") or ep.TYPE_SLIDER),
    }


def parse_check(data: dict[str, Any]) -> str:
    """Return `validate`, or raise the mapped domain error."""
    nested = _first_dict(data.get("data"), data.get("result")) or {}
    validate = _first(data, "validate") or _first(nested, "validate")
    # Absent flags mean "no failure reported"; `result: false` or a non-zero
    # `error` always win over a validate that happens to be in the body.
    failed = data.get("result") is False or data.get("error") not in (None, 0, "0")
    if validate and not failed:
        return str(validate)
    message = str(data.get("msg") or data.get("message") or nested.get("msg") or "")
    if _expired(message) or str(data.get("code") or "") == "2306":
        raise ChallengeExpiredError(
            "yidun check: the round expired before the answer landed",
            context={"msg": message, "error": data.get("error")},
        )
    if validate:
        # A validate without a clean result is not an answer the site can use.
        raise CaptchaError(
            "yidun check returned validate with a failing result",
            context={"result": data.get("result"), "error": data.get("error"), "msg": message},
        )
    raise CaptchaError(
        "yidun check returned no validate",
        context={"keys": sorted(data), "error": data.get("error"), "msg": message},
    )


def _expired(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in ep.EXPIRED_MARKERS)


def _strip_jsonp(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("{") or s.startswith("["):
        return s
    open_paren, close_paren = s.find("("), s.rfind(")")
    if open_paren != -1 and close_paren > open_paren:
        return s[open_paren + 1 : close_paren].strip()
    return s


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _first_dict(*values: Any) -> dict[str, Any] | None:
    for value in values:
        if isinstance(value, dict) and value:
            return value
    return None


def _first_url(value: Any) -> str | None:
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, str) and item.startswith(("http://", "https://")):
            return item
    return None
