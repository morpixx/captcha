"""Shared GeeTest crypto primitives: AES envelope, RSA tail, v3 custom encoder.

Both generations use AES-128-CBC with a 16-ASCII-'0' IV and a 16-character
session key; only v4's first half is the raw hex. The v3 bundle re-encodes the
ciphertext bytes with a modified base64 before sending it, so that function
lives here next to the cipher it wraps.

No I/O, no generation-specific body assembly — 'payload.py' (v4) and
'v3_payload.py' build on this module.
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
from captcha.domain.errors import ConfigError, UnsolvableError

# CryptoJS.enc.Utf8.parse("0000000000000000"): 16 ASCII '0', never random.
AES_IV = b"0000000000000000"

# The public GeeTest RSA key. 'setPublic(N, "10001")' in gt.js / slide.<ver>.js
# and the constructor key in the v4 initEncipher scheme are the same 1024-bit
# modulus. Verbatim from the published bundles (2025-2026); e = 65537.
GEETEST_RSA_MODULUS_HEX = (
    "00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7"
    "977D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF"
    "627715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9"
    "219D0BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81"
)
GEETEST_RSA_EXPONENT_HEX = "10001"

# Modified-base64 alphabet of the v3 '_encode' (64 chars) and its bit masks.
# FILL: confirm the alphabet still starts with "ABCDEF..." in the live
#       slide.<ver>.js; the 2025 articles describe the same numeric-array ->
#       custom-encoder pipeline but only the 6.0.9 source is verbatim public.
V3_ENCODER_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789()"
V3_ENCODER_MASKS = (7274496, 9483264, 19220, 235)
V3_ENCODER_PAD = "."


def need(fields: dict[str, Any]) -> None:
    """Raise the canonical error when a wire field is missing or empty."""
    missing = [k for k, v in fields.items() if v in (None, "")]
    if missing:
        raise UnsolvableError(
            f"geetest payload missing fields: {missing}",
            context={"missing": missing, "have": sorted(fields)},
        )


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def session_key(rand_int: int) -> bytes:
    """16-character hex session key — the shape of the bundle's 'guid()'."""
    return f"{int(rand_int) & 0xFFFF_FFFF_FFFF_FFFF:016x}".encode("ascii")


def encrypt_body_bytes(body: dict[str, Any], key: bytes) -> bytes:
    """JSON.stringify -> AES-128-CBC/PKCS7 with the fixed IV, raw ciphertext.

    Mirrors CryptoJS: no whitespace after separators, UTF-8, PKCS7 padding.
    """
    if len(key) != 16:
        raise UnsolvableError("geetest session key must be 16 bytes", context={"len": len(key)})
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return aes_cbc_encrypt(key, AES_IV, raw)


def encrypt_body(body: dict[str, Any], key: bytes) -> str:
    """Lowercase hex of 'encrypt_body_bytes' (the v4 AES half)."""
    return encrypt_body_bytes(body, key).hex()


def rsa_tail(key: bytes, modulus_hex: str, exponent_hex: str, generation: str) -> str:
    """RSA-PKCS1v15 of the session key, hex — the tail of the first 'w'."""
    if not modulus_hex:
        raise ConfigError(
            f"geetest {generation}: RSA public modulus is not configured, 'w' cannot be completed",
            context={"constant": f"{generation.upper()}_RSA_MODULUS_HEX"},
        )
    public_key = load_public_key_from_modexp(int(modulus_hex, 16), int(exponent_hex, 16))
    return rsa_public_encrypt_pkcs1v15(public_key, key).hex()


def custom_encode(data: bytes) -> str:
    """The v3 '_encode': modified base64 over the ciphertext bytes.

    Each 3-byte block is re-partitioned into four 6-bit groups through fixed
    masks (not the standard base64 order), looked up in the 64-char alphabet;
    a 1/2-byte tail emits 2/3 chars and pads with ".". The result is what the
    bundle calls 'h'; 'w' is 'h + u' (or 'h' alone on the answer round).
    """
    out: list[str] = []
    pad = ""
    index = 0
    while index + 2 < len(data):
        block = (data[index] << 16) | (data[index + 1] << 8) | data[index + 2]
        out.extend(V3_ENCODER_ALPHABET[_masked_bits(block, mask)] for mask in V3_ENCODER_MASKS)
        index += 3
    remainder = len(data) - index
    if remainder == 2:
        block = (data[index] << 16) | (data[index + 1] << 8)
        out.extend(V3_ENCODER_ALPHABET[_masked_bits(block, mask)] for mask in V3_ENCODER_MASKS[:3])
        pad = V3_ENCODER_PAD
    elif remainder == 1:
        block = data[index] << 16
        out.extend(V3_ENCODER_ALPHABET[_masked_bits(block, mask)] for mask in V3_ENCODER_MASKS[:2])
        pad = V3_ENCODER_PAD * 2
    return "".join(out) + pad


def _masked_bits(value: int, mask: int) -> int:
    """Collect the bits of 'value' selected by 'mask', MSB first."""
    out = 0
    for bit in range(23, -1, -1):
        if (mask >> bit) & 1:
            out = (out << 1) + ((value >> bit) & 1)
    return out
