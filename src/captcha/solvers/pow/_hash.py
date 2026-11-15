"""Difficulty math for the Friendly Captcha v1 proof-of-work.

Called once per mined candidate, so it stays allocation-light and importable
without any ML dependency.
"""

from __future__ import annotations

import math

_UINT32_MAX = 0xFFFFFFFF


def difficulty_threshold(difficulty: int) -> int:
    """Difficulty threshold T for puzzle difficulty byte `d` (friendly-pow spec).

    T = floor(2 ** ((255.999 - d) / 8)); a candidate wins when the first four
    bytes of its blake2b-256 digest, read as a little-endian u32, are below T.
    """
    if not 0 <= difficulty <= 255:
        raise ValueError(f"difficulty must be a byte, got {difficulty}")
    return min(int(math.pow(2.0, (255.999 - difficulty) / 8.0)), _UINT32_MAX)


def hash_meets_threshold(digest: bytes, threshold: int) -> bool:
    """True when the first 4 digest bytes (little-endian u32) are below `threshold`."""
    if len(digest) < 4:
        raise ValueError("digest must be at least 4 bytes")
    return int.from_bytes(digest[:4], "little") < threshold


def leading_zero_bits(digest: bytes) -> int:
    """Count leading zero bits in a big-endian byte string (debug helper, not mining)."""
    n = 0
    for b in digest:
        if b == 0:
            n += 8
            continue
        n += 8 - b.bit_length()
        return n
    return n
