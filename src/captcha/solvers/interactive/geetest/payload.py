"""GeeTest v4 'w' assembly, plus re-exports of the shared and v3 helpers.

v4 wraps the same AES envelope differently depending on the 'pt' returned by
'/load':

  * 'pt=0' — 'w' is the URL-encoded raw JSON body.
  * 'pt=1' — 'w = hex(AES-128-CBC(body, uid16, iv="0000000000000000"))
    + hex(RSA-PKCS1v15(uid16))'.
  * 'pt=2' — a different curve scheme; not public, rejected as unsolvable.

The public 2026 implementation this follows is xKiian/GeekedTest
('geeked/sign.py', SDK v1.9.9-53b07e): three 'lot_number' slice groups joined
with ".", 'userresponse = left / 1.0059466666666665 + 2', GET '/verify'.

Every value that still needs a live capture carries a '# FILL:' naming the
exact devtools read that settles it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import quote_plus

from captcha.domain.errors import UnsolvableError

from .v3_payload import (
    V3_RSA_EXPONENT_HEX,
    V3_RSA_MODULUS_HEX,
    V3_SDK_VERSION,
    V3_TRACK_ALPHABET,
    apply_track_cipher,
    build_w_v3,
    early_passtime,
    encode_track,
    imgload_stand_in,
    track_points,
    v3_body,
    v3_params,
    v3_user_response,
)
from .wire import (
    AES_IV,
    GEETEST_RSA_EXPONENT_HEX,
    GEETEST_RSA_MODULUS_HEX,
    V3_ENCODER_ALPHABET,
    V3_ENCODER_MASKS,
    V3_ENCODER_PAD,
    custom_encode,
    encrypt_body,
    encrypt_body_bytes,
    md5_hex,
    need,
    rsa_tail,
    session_key,
)

__all__ = [
    "AES_IV",
    "GEETEST_RSA_EXPONENT_HEX",
    "GEETEST_RSA_MODULUS_HEX",
    "V3_ENCODER_ALPHABET",
    "V3_ENCODER_MASKS",
    "V3_ENCODER_PAD",
    "V3_RSA_EXPONENT_HEX",
    "V3_RSA_MODULUS_HEX",
    "V3_SDK_VERSION",
    "V3_TRACK_ALPHABET",
    "apply_track_cipher",
    "build_w_v3",
    "build_w_v4",
    "custom_encode",
    "early_passtime",
    "encode_track",
    "encrypt_body",
    "encrypt_body_bytes",
    "encrypt_w",
    "imgload_stand_in",
    "lot_fields",
    "md5_hex",
    "need",
    "rsa_tail",
    "session_key",
    "solve_pow",
    "track_points",
    "v3_body",
    "v3_params",
    "v3_user_response",
    "v4_body",
    "v4_passtime",
    "v4_user_response",
]

# v4 body constants observed in the public 2026 signer.
V4_BIHT = "1426265548"
V4_EP = "123"
V4_GEETEST = "captcha"
V4_LANG = "zh"
V4_EM = {"cp": 0, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1}
V4_GEE_GUARD = {
    "roe": {
        "auh": "3",
        "aup": "3",
        "cdc": "3",
        "egp": "3",
        "res": "3",
        "rew": "3",
        "sep": "3",
        "snh": "3",
    }
}

# bzj5 is a per-build obfuscation tag; lot_number yields the extra key through
# three slice groups joined with ".". Ranges are inclusive on both ends, exactly
# as the public LotParser reads 'n[28:29]' (it adds 1 to the end index).
# FILL: re-derive the three expressions if a future gcaptcha4.js changes them;
#       the current set is the working 2026-10 signer.
V4_BZJ5 = "MoPY"
V4_LOT_KEY_GROUPS = (
    ((28, 29), (17, 18)),
    ((5, 10),),
    ((0, 0), (15, 15), (25, 25), (30, 30)),
)
V4_LOT_VALUE_SLICES = ((22, 27),)

# v4 slider distance to server-side 'userresponse' conversion. 1.0059466666666665
# is 0.8876 * 340 / 300 from the bundle; the +2 is the piece offset.
V4_UR_DIVISOR = 1.0059466666666665
V4_UR_OFFSET = 2

V4_RSA_MODULUS_HEX = GEETEST_RSA_MODULUS_HEX
V4_RSA_EXPONENT_HEX = GEETEST_RSA_EXPONENT_HEX


def lot_fields(lot_number: str) -> dict[str, Any]:
    """The extra key the v4 body derives from 'lot_number' by slicing.

    Three character-range groups are joined with "." to build a nested key
    path; the value is one more range. Ranges are inclusive, as the public
    LotParser reads them ('n[28:29]' takes chars 28 and 29).
    """
    if len(lot_number) < 32:
        raise UnsolvableError(
            "geetest v4: lot_number shorter than 32 chars",
            context={"len": len(lot_number)},
        )

    def take(slices: tuple[tuple[int, int], ...]) -> str:
        return "".join(lot_number[start : end + 1] for start, end in slices)

    parts = ".".join(take(group) for group in V4_LOT_KEY_GROUPS).split(".")
    value = take(V4_LOT_VALUE_SLICES)
    root: dict[str, Any] = {}
    node = root
    for part in parts[:-1]:
        node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    return root


def v4_user_response(left: float) -> float:
    """Slider 'userresponse': 'left / 1.0059466666666665 + 2'."""
    return float(left) / V4_UR_DIVISOR + V4_UR_OFFSET


def v4_passtime(measured_ms: float) -> int:
    """Clamp the measured solve time into the range the v4 signer sends."""
    return max(600, min(1200, int(measured_ms)))


def solve_pow(
    lot_number: str,
    captcha_id: str,
    pow_detail: dict[str, Any],
    *,
    seed: int = 0,
    max_attempts: int = 500_000,
) -> tuple[str, str]:
    """Find a uid whose hash carries 'bits' leading zero bits.

    Returns '(pow_msg, pow_sign)', where 'pow_msg' is
    'version|bits|hash|datetime|captcha_id|lot_number||uid'.
    """
    version = str(pow_detail.get("version", "1"))
    bits = int(pow_detail.get("bits", 0) or 0)
    algo = str(pow_detail.get("hashfunc", "md5")).lower()
    stamp = str(pow_detail.get("datetime", ""))
    head = "|".join((version, str(bits), algo, stamp, captcha_id, lot_number, "", ""))
    prefix = "0" * (bits // 4)
    limit = {1: "7", 2: "3", 3: "1"}.get(bits % 4, "")
    hasher = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256}.get(algo)
    if hasher is None:
        raise UnsolvableError("geetest v4 pow: unsupported hash", context={"hashfunc": algo})
    for attempt in range(max_attempts):
        uid = f"{(int(seed) * 1_000_003 + attempt) & 0xFFFF_FFFF_FFFF_FFFF:016x}"
        digest = hasher((head + uid).encode("utf-8")).hexdigest()
        if not digest.startswith(prefix):
            continue
        if not bits % 4 or digest[len(prefix)] <= limit:
            return head + uid, digest
    raise UnsolvableError(
        "geetest v4 pow: no answer within budget",
        context={"bits": bits, "attempts": max_attempts},
    )


def v4_body(
    *,
    lot_number: str,
    captcha_id: str,
    left: float,
    passtime_ms: int,
    pow_msg: str,
    pow_sign: str,
    risk_type: str = "slide",
) -> dict[str, Any]:
    """The JSON gcaptcha4 wraps into 'w' (or urlencodes for pt=0)."""
    need({"lot_number": lot_number, "captcha_id": captcha_id, "pow_msg": pow_msg, "pow_sign": pow_sign})
    if not str(risk_type).startswith("slide"):
        raise UnsolvableError(
            "geetest v4: only the slide risk_type is solvable by CV",
            context={"risk_type": risk_type},
        )
    body: dict[str, Any] = {
        "bzj5": V4_BZJ5,
        "pow_msg": pow_msg,
        "pow_sign": pow_sign,
    }
    body.update(lot_fields(lot_number))
    body.update(
        {
            "biht": V4_BIHT,
            "device_id": "",
            "em": dict(V4_EM),
            "gee_guard": {"roe": dict(V4_GEE_GUARD["roe"])},
            "ep": V4_EP,
            "geetest": V4_GEETEST,
            "lang": V4_LANG,
            "lot_number": lot_number,
            "passtime": int(passtime_ms),
            "setLeft": left,
            "userresponse": v4_user_response(left),
        }
    )
    return body


def encrypt_w(body: dict[str, Any], pt: str, *, key: bytes | None = None, rand_int: int = 0) -> str:
    """Wrap the v4 body according to the load response's 'pt'."""
    mode = str(pt or "0")
    if mode == "0":
        raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        return quote_plus(raw)
    if mode != "1":
        # pt=2 is the createPoint/initEncipher curve scheme; no public Python
        # port exists, so the honest answer is "unsolvable", not a bad 'w'.
        raise UnsolvableError(
            "geetest v4: unsupported payload encryption (pt)",
            context={"pt": mode},
        )
    session = key if key is not None else session_key(rand_int)
    return encrypt_body(body, session) + rsa_tail(session, V4_RSA_MODULUS_HEX, V4_RSA_EXPONENT_HEX, "v4")


def build_w_v4(
    lot_number: str,
    captcha_id: str,
    left: float,
    passtime_ms: int,
    rand_int: int,
    pow_answer: str,
    *,
    pt: str = "1",
    risk_type: str = "slide",
    key: bytes | None = None,
) -> str:
    """Full v4 'w' for the requested 'pt'.

    'pow_answer' is 'pow_msg + "|" + pow_sign'; the split uses the last "|"
    because 'pow_msg' itself contains separators.
    """
    need({"lot_number": lot_number, "captcha_id": captcha_id, "pow_answer": pow_answer})
    pow_msg, _, pow_sign = pow_answer.rpartition("|")
    body = v4_body(
        lot_number=lot_number,
        captcha_id=captcha_id,
        left=left,
        passtime_ms=passtime_ms,
        pow_msg=pow_msg,
        pow_sign=pow_sign,
        risk_type=risk_type,
    )
    return encrypt_w(body, pt, key=key, rand_int=rand_int)
