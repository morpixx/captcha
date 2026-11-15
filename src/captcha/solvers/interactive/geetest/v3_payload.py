"""GeeTest v3 answer assembly: H(), getAA(), track encoding and the two 'w's.

The 2025-2026 v3 flow is five calls: register (site-side; we receive gt +
challenge), get.php (first w, RSA tail), ajax.php (round mode), get.php #2
(images, c/s and a challenge two chars longer), ajax.php #2 (answer w, AES half
only). This module owns everything between the images and those two bodies.
"""

from __future__ import annotations

import math
import random
from typing import Any

from captcha.domain.errors import UnsolvableError

from .wire import (
    GEETEST_RSA_EXPONENT_HEX,
    GEETEST_RSA_MODULUS_HEX,
    custom_encode,
    encrypt_body_bytes,
    md5_hex,
    need,
    rsa_tail,
    session_key,
)

# 'ep.v' in the v3 body. The 2023-2025 bundles version per challenge type
# (slide.<ver>.js), not as one SDK; this stand-in is still unconfirmed.
# FILL: read 'ep.v' from one decrypted v3 verify body on the target site and
#       replace this constant (or pass it from the gettype.php manifest).
V3_SDK_VERSION = "9.0.7"

V3_RSA_MODULUS_HEX = GEETEST_RSA_MODULUS_HEX
V3_RSA_EXPONENT_HEX = GEETEST_RSA_EXPONENT_HEX

# v3 track alphabet (the bundle's delta encoder).
V3_TRACK_ALPHABET = "()*,-./0123456789:?@ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqr"
_V3_MOVE_MAP = {
    (1, 0): "s",
    (2, 0): "t",
    (1, -1): "u",
    (1, 1): "v",
    (0, 1): "w",
    (0, -1): "x",
    (3, 0): "y",
    (2, -1): "z",
    (2, 1): "~",
}


def _rng() -> random.Random:
    return random.SystemRandom()


def v3_user_response(distance: float, challenge: str, rng: random.Random | None = None) -> str:
    """'H(distance, challenge34)' — the v3 'userresponse' field.

    The 34-char challenge is the one returned by the *second* get.php: the last
    two base36 chars encode a per-round offset added to the slider distance; the
    first 32 chars are deduplicated into five round-robin buckets, and the target
    is decomposed with weights 1/2/5/10/50 picking one bucket char per step.
    The picks are random, so the string differs per run while the server-side
    inverse stays valid.
    """
    if len(challenge) < 34:
        raise UnsolvableError(
            "geetest v3: challenge shorter than 34 chars; the second get.php extends it",
            context={"len": len(challenge), "challenge": challenge[:40]},
        )
    tail = challenge[32:34]
    digits = [ord(ch) - 87 if ord(ch) > 57 else ord(ch) - 48 for ch in tail]
    if any(digit < 0 or digit > 35 for digit in digits):
        raise UnsolvableError("geetest v3: challenge tail is not base36", context={"tail": tail})
    target = math.floor(float(distance) + 0.5) + 36 * digits[0] + digits[1]

    groups: list[list[str]] = [[], [], [], [], []]
    seen: set[str] = set()
    slot = 0
    for char in challenge[:32]:
        if char in seen:
            continue
        seen.add(char)
        groups[slot].append(char)
        slot = (slot + 1) % 5

    weights = [1, 2, 5, 10, 50]
    picker = rng or _rng()
    out: list[str] = []
    index = 4
    while target > 0:
        if index < 0:
            raise UnsolvableError(
                "geetest v3: userresponse weights exhausted",
                context={"target": target, "challenge": challenge[:40]},
            )
        if target - weights[index] < 0 or not groups[index]:
            groups.pop(index)
            weights.pop(index)
            index -= 1
            continue
        bucket = groups[index]
        out.append(bucket[int(picker.random() * len(bucket))])
        target -= weights[index]
    return "".join(out)


def track_points(distance: float, rng: random.Random | None = None, *, steps: int | None = None) -> list[list[int]]:
    """Synthetic human-ish pointer track '[[x, y, t_ms], ...]' for 'aa'.

    The server never checks y heavily (public analyses use y=0); what matters is
    monotonic x, a lead-in from outside the widget, and non-uniform timing.
    """
    picker = rng or _rng()
    distance = max(1.0, float(distance))
    if steps is None:
        steps = max(12, min(48, int(distance / 4)))
    points: list[list[int]] = [
        [-25 - picker.randint(0, 5), -30 - picker.randint(0, 5), 0],
        [0, 0, 0],
    ]
    stamp = 0
    for step in range(1, steps + 1):
        progress = step / steps
        eased = progress * progress * (3.0 - 2.0 * progress)  # smoothstep
        x = round(distance * eased)
        y = 0 if picker.random() < 0.85 else picker.choice([-1, 1])
        stamp += picker.randint(16, 48)
        points.append([x, y, stamp])
    points.append([round(distance), 0, stamp + picker.randint(180, 420)])
    return points


def encode_track(points: list[list[int]]) -> str:
    """Encrypt the pointer track the way the v3 bundle does before getAA.

    The result is 'x-deltas !! y-deltas !! time-deltas', where a delta is
    collapsed to one char when it is one of the nine common moves and otherwise
    written with the bundle's signed base-64-ish encoding.
    """
    deltas = _collapse_track(points)
    xs: list[str] = []
    ys: list[str] = []
    times: list[str] = []
    for dx, dy, dt in deltas:
        move = _V3_MOVE_MAP.get((dx, dy))
        if move is not None:
            ys.append(move)
        else:
            xs.append(_track_char(dx))
            ys.append(_track_char(dy))
        times.append(_track_char(dt))
    return "".join(xs) + "!!" + "".join(ys) + "!!" + "".join(times)


def _collapse_track(points: list[list[int]]) -> list[tuple[int, int, int]]:
    deltas: list[tuple[int, int, int]] = []
    carry = 0
    previous = [0, 0, 0]
    for point in points[1:]:
        dx = round(point[0] - previous[0])
        dy = round(point[1] - previous[1])
        dt = round(point[2] - previous[2])
        previous = point
        if dx == 0 and dy == 0 and dt == 0:
            continue
        if dx == 0 and dy == 0:
            carry += dt
        else:
            deltas.append((dx, dy, dt + carry))
            carry = 0
    if carry:
        deltas.append((0, 0, carry))
    return deltas


def _track_char(value: int) -> str:
    negative = value < 0
    magnitude = abs(value)
    size = len(V3_TRACK_ALPHABET)
    high = magnitude // size
    if high >= size:
        high = size - 1
    low = magnitude % size
    out = "!" if negative else ""
    if high > 0:
        out += "$" + V3_TRACK_ALPHABET[high]
    return out + V3_TRACK_ALPHABET[low]


def apply_track_cipher(track: str, c: Any, s: str) -> str:
    """'getAA(track, c, s)': splice each byte of 's' into the encrypted track.

    'c' is the coefficient array from the image response, 's' its hex payload;
    the insertion point is '(c0*x^2 + c2*x + c4) % len(track)', re-evaluated
    after every insert. Missing coefficients or non-hex 's' return the track
    unchanged (the server then sees an un-encrypted 'aa').
    """
    coefficients = _coerce_c(c)
    if not s or len(coefficients) < 5:
        return track
    out = track
    for index in range(0, len(s) - 1, 2):
        pair = s[index : index + 2]
        try:
            code = int(pair, 16)
        except ValueError:
            return track
        position = (coefficients[0] * code * code + coefficients[2] * code + coefficients[4]) % len(out)
        out = out[:position] + chr(code) + out[position:]
    return out


def _coerce_c(value: Any) -> list[int]:
    if isinstance(value, (list, tuple)):
        return [int(item) for item in value]
    if isinstance(value, str):
        body = value.strip().strip("[]")
        if not body:
            return []
        try:
            return [int(item) for item in body.split(",")]
        except ValueError:
            return []
    return []


def v3_body(
    *,
    gt: str,
    challenge: str,
    user_response: str,
    passtime_ms: int,
    imgload_ms: int,
    aa: str = "",
    lang: str = "en",
    last_t: int | None = None,
    ep_version: str = V3_SDK_VERSION,
) -> dict[str, Any]:
    """The JSON gt.js AES-encrypts into the first half of 'w'.

    'rp' covers 'gt + challenge[:32] + passtime' on the first round and
    'gt + challenge + last track t' on the answer round ('last_t' set).
    """
    passtime = int(passtime_ms)
    rp_source = f"{gt}{challenge}{int(last_t)}" if last_t is not None else f"{gt}{challenge[:32]}{passtime}"
    return {
        "lang": lang,
        "userresponse": user_response,
        "passtime": passtime,
        "imgload": int(imgload_ms),
        "aa": aa,
        "ep": {"v": ep_version, "me": True},
        "rp": md5_hex(rp_source),
    }


def build_w_v3(
    gt: str,
    challenge: str,
    user_response: str,
    passtime_ms: int,
    rand_int: int,
    *,
    imgload_ms: int | None = None,
    aa: str = "",
    lang: str = "en",
    last_t: int | None = None,
    with_rsa: bool = True,
    key: bytes | None = None,
) -> str:
    """v3 'w': custom-encoded AES half, plus the RSA tail on the first round.

    'with_rsa=False' is the answer-round 'w' (2025 analyses agree there is no
    RSA half there); 'key' pins the 16-char session key in tests.
    """
    need({"gt": gt, "challenge": challenge})
    session = key if key is not None else session_key(rand_int)
    body = v3_body(
        gt=gt,
        challenge=challenge,
        user_response=user_response,
        passtime_ms=passtime_ms,
        imgload_ms=imgload_ms if imgload_ms is not None else 30 + (int(rand_int) % 120),
        aa=aa,
        lang=lang,
        last_t=last_t,
    )
    encoded = custom_encode(encrypt_body_bytes(body, session))
    if not with_rsa:
        return encoded
    return encoded + rsa_tail(session, V3_RSA_MODULUS_HEX, V3_RSA_EXPONENT_HEX, "v3")


def v3_params(
    gt: str,
    challenge: str,
    *,
    lang: str,
    risk_type: str,
    callback: str,
    w: str | None = None,
) -> dict[str, str]:
    """Query params the v3 widget sends to get.php / ajax.php."""
    params = {
        "gt": gt,
        "challenge": challenge,
        "lang": lang,
        "pt": "0",
        "client_type": "web",
        "callback": callback,
    }
    if risk_type:
        params["risk_type"] = risk_type
    if w is not None:
        params["w"] = w
    return params


def early_passtime() -> int:
    """Plausible pre-solve passtime for the first w (it carries no answer)."""
    return 120 + random.SystemRandom().randrange(200)


def imgload_stand_in() -> int:
    """Fallback imgload when the image fetch is too fast to measure."""
    return 30 + random.SystemRandom().randrange(120)
