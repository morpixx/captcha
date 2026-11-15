"""MurmurHash3 x64-128, the primitive Arkose's BDA derives its hashes with.

The enforcement bundle calls it `K`: a 128-bit MurmurHash3 emitted as
`hex(h1) || hex(h2)`, 32 lowercase hex chars. `f` and `ife_hash` are the only
two values the token endpoint re-checks against the `fe` list it receives, so
this has to be byte-exact rather than "close enough".

Recovered from the deobfuscated `api.js` / VM program (public RE, 2026-07);
reproduces live mints. Nothing here talks to the network.
"""

from __future__ import annotations

import hashlib

_MASK64 = 0xFFFFFFFFFFFFFFFF
_C1 = 0x87C37B91114253D5
_C2 = 0x4CF5AD432745937F


def _rotl64(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (64 - bits))) & _MASK64


def _fmix64(value: int) -> int:
    value = (value ^ (value >> 33)) & _MASK64
    value = (value * 0xFF51AFD7ED558CCD) & _MASK64
    value = (value ^ (value >> 33)) & _MASK64
    value = (value * 0xC4CEB9FE1A85EC53) & _MASK64
    return (value ^ (value >> 33)) & _MASK64


def khash(data: str | bytes, seed: int = 0) -> str:
    """`K(data, seed)` -> 32 hex chars. MurmurHash3 x64-128, seed is h1=h2=seed."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    length = len(raw)
    blocks = length // 16
    h1 = seed & _MASK64
    h2 = seed & _MASK64

    for index in range(blocks):
        offset = index * 16
        k1 = int.from_bytes(raw[offset : offset + 8], "little")
        k2 = int.from_bytes(raw[offset + 8 : offset + 16], "little")

        k1 = _rotl64((k1 * _C1) & _MASK64, 31)
        k1 = (k1 * _C2) & _MASK64
        h1 ^= k1
        h1 = (_rotl64(h1, 27) + h2) & _MASK64
        h1 = (h1 * 5 + 0x52DCE729) & _MASK64

        k2 = _rotl64((k2 * _C2) & _MASK64, 33)
        k2 = (k2 * _C1) & _MASK64
        h2 ^= k2
        h2 = (_rotl64(h2, 31) + h1) & _MASK64
        h2 = (h2 * 5 + 0x38495AB5) & _MASK64

    tail = raw[blocks * 16 :]
    k1 = 0
    k2 = 0

    if len(tail) > 8:
        for position in range(len(tail) - 1, 7, -1):
            k2 ^= tail[position] << ((position - 8) * 8)
        k2 = _rotl64((k2 * _C2) & _MASK64, 33)
        k2 = (k2 * _C1) & _MASK64
        h2 ^= k2

    if tail:
        for position in range(min(len(tail), 8) - 1, -1, -1):
            k1 ^= tail[position] << (position * 8)
        k1 = _rotl64((k1 * _C1) & _MASK64, 31)
        k1 = (k1 * _C2) & _MASK64
        h1 ^= k1

    h1 ^= length
    h2 ^= length
    h1 = (h1 + h2) & _MASK64
    h2 = (h2 + h1) & _MASK64
    h1 = _fmix64(h1)
    h2 = _fmix64(h2)
    h1 = (h1 + h2) & _MASK64
    h2 = (h2 + h1) & _MASK64
    return f"{h1:016x}{h2:016x}"


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def salted(field: str, salt: str) -> str:
    """Per-session field: md5(salt + '|' + field). Keeps cross-fields consistent."""
    return md5_hex(f"{salt}|{field}")


def feature_hash(fe: list[str]) -> str:
    """`f`: K over the fe *values*, ';'-joined, seed 0 (the bundle's KQ())."""
    return khash(";".join(entry.split(":", 1)[-1] for entry in fe), 0)


def ife_hash(fe: list[str]) -> str:
    """`ife_hash`: K over the full fe entries, ', '-joined, seed 38."""
    return khash(", ".join(fe), 38)
