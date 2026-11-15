"""DataDome ``tags.js`` payload builder — pure Python, no browser.

``jspl`` is not crypto. The bundle XORs its signal buffer with a byte stream
from an xorshift PRNG (``n ^= n << 13; n ^= n >> 17; n ^= n << 5``) seeded from
the *public* ``ddjskey``, the constant ``11027890091`` and the request
timestamp, then encodes the result with a custom base64 alphabet. Nothing here
is a secret: the same inputs always produce the same ``jspl``.

Wire facts and the constant come from ``docs/reverse-2026.md`` §5
(dfp-redteam-index, 2026-11: "reversible xorshift-XOR", custom alphabet
``-_0-9A-Za-z``). The bundle's v5.11.x generator was rewritten, so this module
implements the documented scheme and freezes it behind tests — the byte stream
is a port, not a live capture.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

#: Public value baked into every tags.js build; part of the PRNG salt.
DD_SEED_CONST: int = 11027890091

#: ``customHash`` fallback for empty input in the bundle's djb2 variant.
DD_HASH_FALLBACK: int = 1789537805

#: ``encode6Bits`` maps 0..63 onto ``-_0-9A-Za-z`` in this order.
ALPHABET: str = "-_0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_INDEX = {ch: i for i, ch in enumerate(ALPHABET)}

_MASK = 0xFFFFFFFF


def _u32(value: int) -> int:
    return value & _MASK


def mix_int(value: int) -> int:
    """One xorshift32 step: the bundle's ``D(e)`` mixing function."""
    v = _u32(value)
    v ^= _u32(v << 13)
    v ^= v >> 17
    v ^= _u32(v << 5)
    return _u32(v)


def custom_hash(text: str) -> int:
    """djb2 variant used to fold strings into a 32-bit seed.

    ``hash = (hash << 5) - hash + ch``, i.e. ``hash * 31 + ch``; zero folds to
    the bundle's fallback constant so a caller cannot produce a dead PRNG.
    """
    if not text:
        return DD_HASH_FALLBACK
    h = 0
    for ch in text:
        h = _u32((h << 5) - h + ord(ch))
    return h or DD_HASH_FALLBACK


class Prng:
    """Byte-generating PRNG from the bundle's ``I(seed, salt)``.

    Three 8-bit slices are taken out of every state (high, middle, low byte);
    only then is the state mixed again. A per-byte decrementing salt is XORed
    into the output when ``salted`` is on — that is what makes the tail of a
    ``jspl`` differ between two requests with identical signals.
    """

    def __init__(self, seed: int, salt: int = 0, *, salted: bool = True) -> None:
        self._state = _u32(seed)
        self._round = -1
        self._salt = _u32(salt)
        self._salted = salted
        self._cache: int | None = None

    def next(self, cache: bool = False) -> int:
        if self._cache is not None:
            out, self._cache = self._cache, None
            return out
        self._round += 1
        if self._round > 2:
            self._state = mix_int(self._state)
            self._round = 0
        out = self._state >> (16 - 8 * self._round)
        if self._salted:
            self._salt = _u32(self._salt - 1)
            out ^= self._salt
        out &= 0xFF
        if cache:
            self._cache = out
        return out

    def xor(self, data: bytes) -> bytes:
        return bytes(byte ^ self.next() for byte in data)


def encode6bits(value: int) -> str:
    """The bundle's ``N(e)``: 6-bit value -> alphabet character."""
    if not 0 <= value <= 63:
        raise ValueError(f"6-bit value out of range: {value}")
    return ALPHABET[value]


def seed_for(ddjskey: str, timestamp_ms: int) -> int:
    """Fold the public key, the constant and the request time into one seed."""
    return custom_hash(f"{ddjskey}{DD_SEED_CONST}{timestamp_ms}")


def salt_for(timestamp_ms: int) -> int:
    return _u32(timestamp_ms)


def encode_payload(data: bytes, salt: int) -> str:
    """Custom base64: 3-byte groups, each byte XORed with a decrementing salt."""
    out: list[str] = []
    counter = _u32(salt)
    for pos in range(0, len(data), 3):
        group = data[pos : pos + 3]
        word = 0
        for i in range(3):
            counter = _u32(counter - 1)
            byte = group[i] if i < len(group) else 0
            word = (word << 8) | (byte ^ (counter & 0xFF))
        out.append(encode6bits(word >> 18 & 63))
        out.append(encode6bits(word >> 12 & 63))
        out.append(encode6bits(word >> 6 & 63))
        out.append(encode6bits(word & 63))
    remainder = len(data) % 3
    if remainder:
        out = out[: -(3 - remainder)]
    return "".join(out)


def decode_payload(text: str, salt: int) -> bytes:
    """Inverse of :func:`encode_payload`; used by tests and by the decoder."""
    out = bytearray()
    counter = _u32(salt)
    for pos in range(0, len(text), 4):
        chunk = text[pos : pos + 4]
        if len(chunk) < 2:
            raise ValueError("truncated base64 group")
        try:
            values = [_INDEX[ch] for ch in chunk]
        except KeyError as exc:
            raise ValueError(f"character outside the datadome alphabet: {exc.args[0]!r}") from exc
        values.extend([0] * (4 - len(values)))
        word = (values[0] << 18) | (values[1] << 12) | (values[2] << 6) | values[3]
        for i in range(len(chunk) * 3 // 4):
            counter = _u32(counter - 1)
            out.append(((word >> (16 - 8 * i)) & 0xFF) ^ (counter & 0xFF))
    return bytes(out)


def build_jspl(signals: dict[str, Any], *, ddjskey: str, timestamp_ms: int) -> str:
    """Serialize ``signals`` and produce the ``jspl`` field for the /js/ POST."""
    raw = json.dumps(signals, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    salt = salt_for(timestamp_ms)
    return encode_payload(Prng(seed_for(ddjskey, timestamp_ms), salt).xor(raw), salt)


def decode_jspl(jspl: str, *, ddjskey: str, timestamp_ms: int) -> dict[str, Any]:
    """Reverse :func:`build_jspl`; proves the XOR+base64 pair is invertible."""
    salt = salt_for(timestamp_ms)
    raw = Prng(seed_for(ddjskey, timestamp_ms), salt).xor(decode_payload(jspl, salt))
    return json.loads(raw)


def base_signals(
    *,
    ddjskey: str,
    cid: str,
    hsh: str,
    page_url: str,
    user_agent: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Signals the tags endpoint reads back; task payload overrides the rest."""
    signals: dict[str, Any] = {
        "cid": cid,
        "ddk": ddjskey,
        "hsh": hsh,
        "url": page_url,
        "ua": user_agent,
    }
    if extra:
        signals.update(extra)
    return signals


def build_tags_body(
    *,
    ddjskey: str,
    cid: str,
    hsh: str,
    page_url: str,
    user_agent: str,
    signals: dict[str, Any] | None = None,
    js_type: str = "ch",
    timestamp_ms: int,
) -> dict[str, Any]:
    """JSON body for ``POST https://api-js.datadome.co/js/``.

    Field names are the ones the live endpoint parses: ``jspl, cid, ddk, hsh,
    responsePage, ua`` plus the ``jsType`` discriminator.
    """
    merged = base_signals(
        ddjskey=ddjskey,
        cid=cid,
        hsh=hsh,
        page_url=page_url,
        user_agent=user_agent,
        extra=signals,
    )
    return {
        "jspl": build_jspl(merged, ddjskey=ddjskey, timestamp_ms=timestamp_ms),
        "cid": cid,
        "ddk": ddjskey,
        "hsh": hsh,
        "responsePage": page_url,
        "ua": user_agent,
        "jsType": js_type,
    }


def datadome_from_body(body: bytes | str | None, names: Iterable[str] = ("datadome",)) -> dict[str, str]:
    """Some deployments answer the tags POST with a JSON ``cookie`` field.

    Returns only the requested cookie names; anything else the server sets is
    not part of the answer a client can use on its own request.
    """
    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    candidates: list[str] = []
    for key in ("cookie", "cookies", "datadome"):
        value = parsed.get(key)
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(str(item) for item in value)
        elif isinstance(value, dict):
            candidates.extend(f"{name}={val}" for name, val in value.items())
    wanted = set(names)
    out: dict[str, str] = {}
    for raw in candidates:
        for pair in str(raw).split(";"):
            name, _, value = pair.strip().partition("=")
            if name.strip() in wanted and value:
                out[name.strip()] = value.strip()
    return out
