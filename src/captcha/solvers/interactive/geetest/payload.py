"""GeeTest `w` envelope — v3 and v4, pure functions, no I/O.

Both generations send the answer wrapped the same way:

    w = hex( AES-128-CBC( json_body, key = 16-character session key,
                          iv  = "0000000000000000" ) )
        + hex( RSA-PKCS1v15( session key ) )

The AES half is `h` in the bundles, the RSA half `u`; the client sends
`h + u` as `w` (gt.js: `w = h + u`, gcaptcha4: `arrayToHex(u) + _`). The
server decrypts with its own private key, so the session key is free-form — it
only has to be 16 characters, which is why `rand_int` is enough to keep the
functions deterministic in tests.

Every value below that was NOT verified against a live site carries a
`# FILL:` naming the exact devtools capture that would settle it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from captcha.crypto import (
    aes_cbc_encrypt,
    load_public_key_from_modexp,
    rsa_public_encrypt_pkcs1v15,
)
from captcha.domain.errors import UnsolvableError

# ---- constants --------------------------------------------------------------

# CryptoJS.enc.Utf8.parse("0000000000000000"): 16 ASCII '0', never random.
# FILL: open the live bundle (slide.<ver>.js for v3, gcaptcha4.js for v4), search
#       for the literal "0000000000000000" and confirm it is still the CBC IV and
#       16 ASCII zero characters (not the 16-byte buffer 00 00 ... 00).
AES_IV = b"0000000000000000"

# FILL: v3 RSA public modulus — open slide.<ver>.js (fetched after the first
#       ajax.php round), search for "setPublic(", copy the FIRST argument (hex,
#       ~512 chars) into V3_RSA_MODULUS_HEX. The second argument is the exponent;
#       "10001" (65537) is what every observed build uses.
V3_RSA_MODULUS_HEX = ""
V3_RSA_EXPONENT_HEX = "10001"

# FILL: v4 RSA public modulus — same capture in gcaptcha4.js: search for the
#       initEncipher/encryptBlock pair, the modulus sits in its constructor.
V4_RSA_MODULUS_HEX = ""
V4_RSA_EXPONENT_HEX = "10001"

# Version string the widget reports in `ep` (v3).
# FILL: read it from the live Network tab — the `v` key inside the decrypted
#       `ep` object of one successful ajax.php verify request.
V3_SDK_VERSION = "9.0.7"

# v4 body constants. Observed values from a 2025 build, not re-verified here.
# FILL: capture the decrypted v4 `w` body of one successful verify (breakpoint
#       in gcaptcha4.js right before the AES call), then confirm or replace
#       `biht`, `ep`, `geetest` and the `em`/`gee_guard` maps.
V4_BIHT = "1426265548"
V4_EP = "123"
V4_GEETEST = "captcha"
V4_LANG = "zh"
V4_EM = {"cp": 0, "ek": "11", "nt": 0, "ph": 0, "sc": 0, "si": 0, "wd": 1}
V4_GEE_GUARD = {"roe": {"auh": "3", "aup": "3", "cdc": "3", "egp": "3", "res": "3", "rew": "3", "sep": "3", "snh": "3"}}

# bzj5 is a per-build obfuscation tag; `lot_number` also yields two more pairs
# through a slice expression that every SDK release rewrites.
# FILL: capture the decrypted v4 `w` body once, confirm "bzj5"/"MoPY" still
#       exists, and re-derive the slices (search gcaptcha4.js for "lot_number",
#       follow the key string built from lot_number[..] slices).
V4_BZJ5 = "MoPY"
V4_LOT_KEY_GROUPS = (((28, 29), (17, 18)), ((5, 10),))
V4_LOT_VALUE_SLICES = ((22, 27),)


def _need(fields: dict[str, Any]) -> None:
    """Raise the canonical error when a wire field is missing or empty."""
    missing = [k for k, v in fields.items() if v in (None, "")]
    if missing:
        raise UnsolvableError(
            f"geetest payload missing fields: {missing}",
            context={"missing": missing, "have": sorted(fields)},
        )


# ---- shared crypto ----------------------------------------------------------


def session_key(rand_int: int) -> bytes:
    """16-character hex session key — the shape of the bundle's `guid()`."""
    return f"{int(rand_int) & 0xFFFF_FFFF_FFFF_FFFF:016x}".encode("ascii")


def encrypt_body(body: dict[str, Any], key: bytes) -> str:
    """JSON.stringify -> AES-128-CBC/PKCS7 with the fixed IV -> lowercase hex.

    Mirrors CryptoJS: no whitespace after separators, UTF-8, PKCS7 padding.
    """
    if len(key) != 16:
        raise UnsolvableError("geetest session key must be 16 bytes", context={"len": len(key)})
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return aes_cbc_encrypt(key, AES_IV, raw).hex()


def _rsa_half(key: bytes, modulus_hex: str, exponent_hex: str, generation: str) -> str:
    """RSA-PKCS1v15 of the session key, hex — the tail of `w`."""
    if not modulus_hex:
        raise UnsolvableError(
            f"geetest {generation}: RSA public modulus not captured, `w` cannot be completed",
            context={
                "fill": f"{generation.upper()}_RSA_MODULUS_HEX in solvers/interactive/geetest/payload.py",
                "capture": "search the live bundle for setPublic(<N>,<e>) and copy N",
            },
        )
    public_key = load_public_key_from_modexp(int(modulus_hex, 16), int(exponent_hex, 16))
    return rsa_public_encrypt_pkcs1v15(public_key, key).hex()


def _md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ---- v3 ---------------------------------------------------------------------


def v3_body(
    *,
    gt: str,
    challenge: str,
    user_response: int | float,
    passtime_ms: int,
    rand_int: int,
) -> dict[str, Any]:
    """The JSON gt.js AES-encrypts into the first half of `w`.

    Field order mirrors the bundle. The server decrypts, so order is cosmetic,
    but keeping it makes a byte-diff against a captured `w` readable.
    """
    passtime = int(passtime_ms)
    return {
        "lang": "en",
        # FILL: the real `userresponse` is H(distance, challenge), a
        #       challenge-keyed transform in slide.<ver>.js (~line 704/159), not
        #       the raw offset. Capture one live triple (slider distance,
        #       challenge, decrypted userresponse) and port H; until then this
        #       field carries the plain offset.
        "userresponse": user_response,
        "passtime": passtime,
        # FILL: `imgload` is the ms delta between requesting bg/slice and the
        #       load event. Plausible values pass, but read the real range off a
        #       decrypted body; below is a deterministic stand-in.
        "imgload": 30 + (int(rand_int) % 120),
        # FILL: `aa` is getAA(track, c, s) — `c` and `s` come from the image
        #       challenge response, `track` is the pointer path. Capture all
        #       three plus the getAA body from the live bundle and port it. An
        #       empty `aa` fails the risk check on most sites.
        "aa": "",
        # FILL: `ep` carries a build-specific `v`, a per-build marker and `tm`
        #       (a performance.timing delta). Capture the decrypted ep object.
        "ep": {"v": V3_SDK_VERSION, "me": True},
        # rp = md5(gt + challenge[:32] + passtime) — reversed, stable per build.
        "rp": _md5_hex(f"{gt}{challenge[:32]}{passtime}"),
    }


def build_w_v3(
    gt: str,
    challenge: str,
    user_response: int | float,
    passtime_ms: int,
    rand_int: int,
    *,
    key: bytes | None = None,
) -> str:
    """Full v3 `w`: hex(AES body) + hex(RSA session key)."""
    _need({"gt": gt, "challenge": challenge})
    session = key if key is not None else session_key(rand_int)
    body = v3_body(
        gt=gt,
        challenge=challenge,
        user_response=user_response,
        passtime_ms=passtime_ms,
        rand_int=rand_int,
    )
    return encrypt_body(body, session) + _rsa_half(session, V3_RSA_MODULUS_HEX, V3_RSA_EXPONENT_HEX, "v3")


# ---- v4 ---------------------------------------------------------------------


def lot_fields(lot_number: str) -> dict[str, str]:
    """The extra pair the v4 body derives from `lot_number` by slicing.

    The bundle builds the key from two groups of character ranges joined with
    "."; the value is one range. Ranges are inclusive, as in the JS slices.
    """

    def take(slices: tuple[tuple[int, int], ...]) -> str:
        return "".join(lot_number[start : end + 1] for start, end in slices)

    key = ".".join(take(group) for group in V4_LOT_KEY_GROUPS)
    return {key: take(V4_LOT_VALUE_SLICES)}


def solve_pow(
    lot_number: str,
    captcha_id: str,
    pow_detail: dict[str, Any],
    *,
    seed: int = 0,
    max_attempts: int = 500_000,
) -> tuple[str, str]:
    """Find a uid whose hash carries `bits` leading zero bits.

    Returns `(pow_msg, pow_sign)`, where `pow_msg` is
    `version|bits|hash|datetime|captcha_id|lot_number||uid`.
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
    user_response: int | float,
    passtime_ms: int,
    pow_msg: str,
    pow_sign: str,
) -> dict[str, Any]:
    """The JSON gcaptcha4 AES-encrypts into `w`."""
    _need({"lot_number": lot_number, "captcha_id": captcha_id, "pow_msg": pow_msg})
    body: dict[str, Any] = {
        "setLeft": user_response,
        "passtime": int(passtime_ms),
        "userresponse": user_response,
        "device_id": "",
        "lot_number": lot_number,
        "pow_msg": pow_msg,
        "pow_sign": pow_sign,
        "geetest": V4_GEETEST,
        "lang": V4_LANG,
        "ep": V4_EP,
        "biht": V4_BIHT,
        "gee_guard": {"roe": dict(V4_GEE_GUARD["roe"])},
        "em": dict(V4_EM),
        "bzj5": V4_BZJ5,
    }
    body.update(lot_fields(lot_number))
    return body


def build_w_v4(
    lot_number: str,
    captcha_id: str,
    user_response: int | float,
    passtime_ms: int,
    rand_int: int,
    pow_answer: str,
) -> str:
    """Full v4 `w`: hex(AES body) + hex(RSA session key).

    `pow_answer` is `pow_msg + "|" + pow_sign` — the proof-of-work preimage and
    its digest. The split uses the last "|" because `pow_msg` itself contains
    "|" separators between its fields.
    """
    _need({"lot_number": lot_number, "captcha_id": captcha_id, "pow_answer": pow_answer})
    pow_msg, _, pow_sign = pow_answer.rpartition("|")
    session = session_key(rand_int)
    body = v4_body(
        lot_number=lot_number,
        captcha_id=captcha_id,
        user_response=user_response,
        passtime_ms=passtime_ms,
        pow_msg=pow_msg,
        pow_sign=pow_sign,
    )
    return encrypt_body(body, session) + _rsa_half(session, V4_RSA_MODULUS_HEX, V4_RSA_EXPONENT_HEX, "v4")
