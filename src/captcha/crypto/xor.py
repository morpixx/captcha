"""Rotating-key XOR. The one in Kasada/BDA sensor framing, and friends.

`xor_cipher` is self-inverse: encrypt == decrypt. Empty key raises.
"""

from __future__ import annotations

from .aes import CryptoError


def xor_cipher(data: bytes, key: bytes) -> bytes:
    """data XOR (key repeated). Fastest with a long key; small keys pay ~0 overhead."""
    if not key:
        raise CryptoError("xor key is empty")
    if not data:
        return b""
    # Build a repeat buffer at least as long as data to vectorise on bytes().
    reps = (len(data) + len(key) - 1) // len(key)
    stream = (key * reps)[: len(data)]
    return bytes(a ^ b for a, b in zip(data, stream, strict=True))


def xor_with_counter(data: bytes, key: bytes, *, start: int = 0) -> bytes:
    """XOR with key, then additively rotate the key index by `start`. Some sensor
    schemes derive a per-chunk offset — model it by setting `start`."""
    if not key:
        raise CryptoError("xor key is empty")
    k = len(key)
    return bytes(b ^ key[(i + start) % k] for i, b in enumerate(data))


def xor_evolving_key(data: bytes, seed: int, *, mul: int = 1103515245, add: int = 12345, mod: int = 2**32) -> bytes:
    """LCG-driven keystream XOR. Parameters default to glibc rand(). Kasada-ish
    obfuscators lean on this when they want a 'key' that is really just a seed."""
    out = bytearray(len(data))
    state = seed & (mod - 1)
    for i, b in enumerate(data):
        state = (state * mul + add) % mod
        out[i] = b ^ (state & 0xFF)
    return bytes(out)
