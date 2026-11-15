"""PerimeterX payload codec.

The sensorless collector POST carries the fields this module owns:

`en`        base64 of the XOR key name (`"50"`). The bundle declares which
            key it obfuscated the payload with; we declare the same.
`payload`   JSON -> XOR "50" -> XOR "10" -> base64 (latin-1) -> swap
            adjacent characters -> base64 -> percent-encoding.
`pc`        HMAC-MD5 keyed with `uuid:tag:ft` over the JSON payload, folded to
            10-11 digits: hex first, a..f remapped to 7,8,9,0,1,2, then even
            indices of the resulting digit string.
`sid`       a visible id with its payload shifted into the Unicode tag block
            U+E0000..U+E007F -- the reason a PX `sid` reads as an empty string in a
            browser but carries 40+ bytes.

Response side: `{"ob": "<base64>"}` decodes through the same XOR pair to a
`~~~~`-separated list of `name|arg|...` segments, from which the `_px*` cookies
come back.

Sources: docs/reverse-2026.md §7 (waterbili/PerimeterX_RE, 2026-05, validated
on iFood and Grubhub). Three details the writeup leaves implicit are called out
inline; each one is frozen by a test vector in `tests/engines` so a drift shows up
as a diff instead of a quiet rejection in production.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_from_bytes, unquote_to_bytes

from captcha.crypto import hmac_md5, xor_cipher
from captcha.domain.errors import SensorError

#: Key name the bundle announces in `en`; also the first XOR key of the payload.
EN_KEY_NAME = "50"

#: XOR keys, in the order the bundle applies them: "50" first, then "10".
#: Both are two ASCII bytes, not the single byte 0x50/0x10.
PAYLOAD_FIRST_KEY = b"50"
PAYLOAD_SECOND_KEY = b"10"

#: Field separator inside a decoded `ob` answer.
OB_SEPARATOR = "~~~~"
SEGMENT_FIELD = "|"

#: Unicode tag block the `sid` payload is shifted into.
TAG_BASE = 0xE0000
TAG_MIN = TAG_BASE + 0x20
TAG_MAX = TAG_BASE + 0x7E

#: a..f -> 7,8,9,0,1,2, the substitution the `pc` fold uses.
PC_HEX_MAP = str.maketrans("abcdef", "789012")

#: iFood deployments truncate `pc` to 10, Grubhub to 11; 10 is the observed
#: default and the caller can override the width.
DEFAULT_PC_LEN = 10

#: Cookies a collector answer can bake. Anything starting with `_px` or equal to
#: `pxcts` is treated as a cookie by :func:`cookies_from_ob`.
PX_COOKIE_PREFIXES: tuple[str, ...] = ("_px", "pxcts")
_PX_COOKIE_NAMES: frozenset[str] = frozenset({"_px", "_px2", "_px3", "_pxhd", "_pxvid", "_pxde", "pxcts"})


@dataclass(frozen=True, slots=True)
class ObSegment:
    """One `name|arg|...` row out of a decoded `ob`."""

    name: str
    args: tuple[str, ...] = ()
    raw: str = ""

    @property
    def value(self) -> str:
        return self.args[0] if self.args else ""


def en_field() -> str:
    """The `en` field: base64 of `"50"`."""
    return base64.b64encode(EN_KEY_NAME.encode("ascii")).decode("ascii")


def swap_pairs(text: str) -> str:
    """Swap characters two by two; a trailing odd character stays put."""
    chars = list(text)
    for i in range(0, len(chars) - 1, 2):
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def unswap_pairs(text: str) -> str:
    """The swap is its own inverse; kept as a named operation for the decoder."""
    return swap_pairs(text)


def encode_payload(payload: Mapping[str, Any]) -> str:
    """Serialize a sensor payload the way the collector expects it."""
    raw = json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=True).encode("ascii")
    inner = xor_cipher(xor_cipher(raw, PAYLOAD_FIRST_KEY), PAYLOAD_SECOND_KEY)
    swapped = swap_pairs(base64.b64encode(inner).decode("ascii"))
    return quote_from_bytes(base64.b64encode(swapped.encode("ascii")), safe="")


def decode_payload(encoded: str) -> dict[str, Any]:
    """Inverse of :func:`encode_payload`; used by tests and error triage."""
    if not encoded:
        raise SensorError("perimeterx: empty payload", context={"engine": "perimeterx"})
    outer = _b64decode(encoded)
    inner_text = unswap_pairs(outer.decode("ascii", errors="replace"))
    combined = _b64decode(inner_text)
    raw = xor_cipher(xor_cipher(combined, PAYLOAD_SECOND_KEY), PAYLOAD_FIRST_KEY)
    try:
        decoded = json.loads(raw)
    except ValueError as exc:
        raise SensorError(
            "perimeterx: payload did not decode to JSON",
            context={"bytes": len(raw), "head": raw[:120].decode("ascii", errors="replace")},
        ) from exc
    if not isinstance(decoded, dict):
        raise SensorError(
            "perimeterx: payload decoded to a non-object",
            context={"got": type(decoded).__name__},
        )
    return decoded


def pc_token(
    *,
    uuid: str,
    tag: str,
    ft: int | str,
    payload_json: str,
    length: int = DEFAULT_PC_LEN,
) -> str:
    """The `pc` challenge-response hash.

    Salt layout `uuid:tag:ft` is the one the writeup gives; the *message* the
    bundle hashes is the serialized payload it is about to send, which is what
    makes `pc` move when the signals do. Both halves are frozen by a vector.
    """
    salt = f"{uuid}:{tag}:{ft}".encode()
    digest = hmac_md5(salt, payload_json.encode("utf-8")).hex()
    folded = digest.translate(PC_HEX_MAP)[::2]
    return folded[: max(1, length)]


def sid_encode(text: str) -> str:
    """Shift printable ASCII into the Unicode tag block (invisible in a page)."""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        shifted = TAG_BASE + code
        out.append(chr(shifted) if TAG_MIN <= shifted <= TAG_MAX else ch)
    return "".join(out)


def sid_decode(text: str) -> str:
    """Inverse of :func:`sid_encode`; visible characters pass through."""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if TAG_MIN <= code <= TAG_MAX:
            out.append(chr(code - TAG_BASE))
        else:
            out.append(ch)
    return "".join(out)


def decode_ob(ob: str | bytes | None) -> str:
    """Base64 + XOR an `ob` answer back to its `~~~~`-separated plaintext.

    The writeup states the XOR pair but not the order the server applies it in,
    so both orders are tried and the first printable-armored result wins; if
    neither looks like a segment list the raw bytes are returned (latin-1), which
    keeps triage possible without pretending to understand them.
    """
    if not ob:
        return ""
    raw = _b64decode(ob)
    candidates = (
        xor_cipher(xor_cipher(raw, PAYLOAD_SECOND_KEY), PAYLOAD_FIRST_KEY),
        xor_cipher(xor_cipher(raw, PAYLOAD_FIRST_KEY), PAYLOAD_SECOND_KEY),
    )
    for candidate in candidates:
        text = candidate.decode("latin-1")
        if SEGMENT_FIELD in text and _printable_ratio(text) >= 0.9:
            return text
    return raw.decode("latin-1")


def parse_ob(ob: str | bytes | None) -> list[ObSegment]:
    """Split a decoded `ob` into `name|arg|...` segments."""
    text = ob if isinstance(ob, str) and SEGMENT_FIELD in ob else decode_ob(ob)
    segments: list[ObSegment] = []
    for chunk in text.split(OB_SEPARATOR):
        chunk = chunk.strip()
        if not chunk:
            continue
        fields = [field.strip() for field in chunk.split(SEGMENT_FIELD)]
        name = fields[0]
        if not name:
            continue
        segments.append(ObSegment(name=name, args=tuple(fields[1:]), raw=chunk))
    return segments


def cookies_from_ob(segments: Iterable[ObSegment]) -> dict[str, str]:
    """Cookie jar a collector answer hands back.

    Two shapes reach the client: a segment whose *name* is the cookie
    (`_pxhd|value`) and a segment that carries a `a=b; c=d` pair list under a
    `cookie`-ish name. Both are accepted; anything else is ignored on purpose --
    baking an unrelated name into the client's jar is worse than dropping it.
    """
    jar: dict[str, str] = {}
    for segment in segments:
        name = segment.name
        value = segment.value
        if _is_px_cookie(name) and value:
            jar[name] = value
            continue
        if name.lower() in ("cookie", "cookies", "bake", "set-cookie") and "=" in value:
            jar.update(cookie_pairs(value))
    return jar


def cookie_pairs(raw: str) -> dict[str, str]:
    """`a=b; c=d` -> dict; attributes and empty values are skipped."""
    out: dict[str, str] = {}
    for part in str(raw or "").split(";"):
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name or not value:
            continue
        if name.lower() in ("path", "domain", "expires", "max-age", "samesite", "secure", "httponly"):
            continue
        out[name] = value
    return out


def _is_px_cookie(name: str) -> bool:
    return name in _PX_COOKIE_NAMES or name.startswith(PX_COOKIE_PREFIXES)


def _b64decode(raw: str | bytes) -> bytes:
    """Tolerant base64: strips percent-encoding, restores missing padding."""
    data = unquote_to_bytes(raw) if isinstance(raw, str) else bytes(raw)
    data = data.strip()
    if not data:
        raise SensorError("perimeterx: empty base64 blob", context={"engine": "perimeterx"})
    padded = data + b"=" * (-len(data) % 4)
    try:
        return base64.b64decode(padded, validate=False)
    except ValueError as exc:
        raise SensorError(
            "perimeterx: blob is not base64",
            context={"bytes": len(data), "head": data[:32].decode("latin-1")},
        ) from exc


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\r\n\t")
    return printable / len(text)


__all__ = [
    "DEFAULT_PC_LEN",
    "EN_KEY_NAME",
    "OB_SEPARATOR",
    "PAYLOAD_FIRST_KEY",
    "PAYLOAD_SECOND_KEY",
    "PX_COOKIE_PREFIXES",
    "SEGMENT_FIELD",
    "ObSegment",
    "cookie_pairs",
    "cookies_from_ob",
    "decode_ob",
    "decode_payload",
    "en_field",
    "parse_ob",
    "pc_token",
    "sid_decode",
    "sid_encode",
    "swap_pairs",
    "unswap_pairs",
]
