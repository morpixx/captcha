"""Bit-level helpers for PoW difficulty checks. Hot path — keep allocation-free."""

from __future__ import annotations


def leading_zero_bits(digest: bytes) -> int:
    """Count leading zero bits in a big-endian byte string.

    sha256 digests come out big-endian (first byte is MSB), so this is what the
    Friendly Captcha spec compares against `difficulty`.
    """
    n = 0
    for b in digest:
        if b == 0:
            n += 8
            continue
        n += 8 - b.bit_length()
        return n
    return n


def count_zero_bytes(digest: bytes) -> int:
    """Count leading zero BYTES. Cheaper threshold for coarse puzzles."""
    n = 0
    for b in digest:
        if b != 0:
            return n
        n += 1
    return n
