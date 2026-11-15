"""Tencent wire codecs — pure functions over the bytes the vendor sent.

Three jobs:

* `parse_reply`: Tencent answers JSONP (`callback({...})`) on most builds and
  plain JSON on the direct-call ones; both must decode, and neither may be
  treated as success by accident.
* `solve_pow`: `cap_union_new_show` hands out `pow_prefix`/`pow_difficulty`;
  the answer is the decimal counter whose `sha256(prefix + counter)` starts
  with the advertised number of hex zeros. Pure Python, no JS.
* `format_ans` / `check_verify`: the geometry encoding the solve produces and
  the ticket/randstr pair, with vendor errors translated into domain errors.

The TDC `collect` blob is *not* here: it is a browser fingerprint, and
faking it is the one thing this solver refuses to do (see `solver._collect`).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from captcha.domain import SolverResult
from captcha.domain.enums import CaptchaType
from captcha.domain.errors import CaptchaError, ChallengeExpiredError, SensorError, UnsolvableError

from . import endpoints as ep

#: Milliseconds budget for one PoW round. The vendor's own `pow_calc_time` is
#: in this unit, so the number is a statement, not a timeout.
MAX_POW_ITERATIONS = 2_000_000


@dataclass(frozen=True, slots=True)
class Round:
    """What `prehandle` + `new_show` told us about this challenge."""

    sess: str
    pic1: str | None = None
    pic2: str | None = None
    pow_prefix: str = ""
    pow_difficulty: Any = ""
    #: Fields the SDK would echo back on verify (vsig, websig, eks, tlg, subsid…).
    echo: dict[str, Any] = field(default_factory=dict)

    @property
    def has_piece(self) -> bool:
        return bool(self.pic2)


def parse_reply(text: str) -> dict[str, Any]:
    """Decode a Tencent reply: JSONP wrapper optional, non-objects are errors."""
    body = _strip_jsonp(text)
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise CaptchaError("tencent reply is not JSON", context={"head": (text or "")[:160]}) from exc
    if not isinstance(data, dict):
        raise CaptchaError("tencent reply is not a JSON object", context={"type": type(data).__name__})
    return data


def parse_round(prehandle: dict[str, Any], show: dict[str, Any] | None = None) -> Round:
    """Fold the two bootstrap replies into one round; missing `sess` is fatal."""
    merged: dict[str, Any] = {}
    for source in (prehandle, show or {}):
        merged.update({k: v for k, v in source.items() if v not in (None, "", [], {})})

    sess = _first(merged, "sess", "sid", "session")
    if not sess:
        raise UnsolvableError(
            "tencent prehandle returned no sess",
            context={"keys": sorted(prehandle), "show_keys": sorted(show or {})},
        )
    return Round(
        sess=str(sess),
        pic1=_url(merged.get("cdnPic1") or merged.get("bg")),
        pic2=_url(merged.get("cdnPic2") or merged.get("piece")),
        pow_prefix=str(merged.get("pow_prefix") or ""),
        pow_difficulty=merged.get("pow_difficulty") or "",
        echo={k: v for k, v in merged.items() if k in _ECHO_FIELDS},
    )


#: Fields the SDK echoes from show into verify, minus the ones we compute.
_ECHO_FIELDS = ("subsid", "vsig", "websig", "eks", "tlg", "spt", "fpinfo", "cdata", "rnd", "uid", "lang")


def pow_zeros(difficulty: Any) -> int:
    """Leading hex zeros the vendor asks for: `"10000"` is four, `"100000"` five."""
    text = str(difficulty or "").strip()
    if not text.isdigit():
        return 0
    return max(len(text) - 1, 0)


def solve_pow(prefix: str, difficulty: Any, *, max_iterations: int = MAX_POW_ITERATIONS) -> tuple[str, int]:
    """Return `(pow_answer, pow_calc_time_ms)`.

    `pow_answer` is the counter, not the digest: the vendor recomputes
    `sha256(pow_prefix + pow_answer)` server-side. A difficulty of zero means
    the round needs no work, and then the answer is `"0"` with no spent time.
    """
    zeros = pow_zeros(difficulty)
    started = time.monotonic()
    if zeros == 0:
        return "0", 0
    target = "0" * zeros
    for counter in range(max_iterations):
        if hashlib.sha256(f"{prefix}{counter}".encode()).hexdigest().startswith(target):
            return str(counter), int((time.monotonic() - started) * 1000)
    raise SensorError(
        "tencent PoW did not converge inside the iteration budget",
        context={"zeros": zeros, "attempts": max_iterations, "prefix_len": len(prefix)},
    )


def format_ans(cv: SolverResult, kind: CaptchaType, *, y: int = 0) -> str:
    """Encode the CV answer the way `ans` is shaped for this challenge type.

    Slider: `"<x>,<y>"` where x is the drag distance and y the piece's row.
    Click: `"x1,y1|x2,y2|x3,y3"` in the order the prompt asks for.
    """
    if kind is CaptchaType.SLIDER:
        return f"{round(cv.offset or 0)},{y}"
    points = cv.coords or []
    if not points:
        raise UnsolvableError("tencent click task: CV delegate returned no points", context={})
    return "|".join(f"{round(x)},{round(yy)}" for x, yy in points)


def verify_body(round_: Round, ans: str, collect: str, *, aid: str, showtype: str = "popup") -> dict[str, Any]:
    """Build the `cap_union_new_verify` form body.

    Only fields that are actually known go out: an empty `collect` or
    `pow_answer` is the vendor's own "missing fingerprint" signal, and sending
    it gets an errorCode that lies about the answer being wrong.
    """
    body: dict[str, Any] = {
        "aid": aid,
        "sess": round_.sess,
        "ans": ans,
        "collect": collect,
        "showtype": showtype,
        "accver": 1,
        "protocol": "https",
    }
    if round_.pow_prefix or round_.pow_difficulty:
        answer, calc_ms = solve_pow(round_.pow_prefix, round_.pow_difficulty)
        body["pow_answer"] = answer
        body["pow_calc_time"] = calc_ms
    body.update({k: v for k, v in round_.echo.items() if k not in body})
    return body


def parse_verify(data: dict[str, Any]) -> tuple[str, str]:
    """Return `(ticket, randstr)` or raise the mapped domain error."""
    code = str(data.get("errorCode", data.get("ret", "")) or "")
    ticket = _str(data.get("ticket"))
    randstr = _str(data.get("randstr"))
    if ticket and (code in ("", "0")):
        return ticket, randstr
    message = str(data.get("errorMessage") or data.get("errMsg") or "")
    if code in ep.EXPIRED_CODES or _mentions(message, ("expire", "timeout", "invalid sess")):
        raise ChallengeExpiredError(
            "tencent verify: the round expired before the answer landed",
            context={"errorCode": code, "errorMessage": message},
        )
    if code in ep.SENSOR_CODES or _mentions(message, ("collect", "tdc", "fingerprint")):
        raise SensorError(
            "tencent verify rejected the TDC fingerprint",
            context={"errorCode": code, "errorMessage": message},
        )
    if ticket and code:
        # Ticket without a clean code is not a success; the client would submit
        # a token the vendor never accepted.
        raise CaptchaError(
            "tencent verify returned a ticket with a non-zero errorCode",
            context={"errorCode": code, "errorMessage": message},
        )
    raise CaptchaError(
        "tencent verify returned no ticket",
        context={"errorCode": code, "errorMessage": message, "keys": sorted(data)},
    )


def _strip_jsonp(text: str) -> str:
    """Return the JSON payload of a reply, unwrapping `cb({...});` when present."""
    s = (text or "").strip()
    if s.startswith("{") or s.startswith("["):
        return s
    open_paren, close_paren = s.find("("), s.rfind(")")
    if open_paren != -1 and close_paren > open_paren:
        return s[open_paren + 1 : close_paren].strip()
    return s


def _mentions(text: str, needles: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(needle in low for needle in needles)


def _url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return None


def _str(value: Any) -> str:
    return str(value) if value not in (None, "") else ""


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
