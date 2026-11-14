"""Deterministic PoW challenge generators. Test-only; never registered."""

from __future__ import annotations

import hashlib
import secrets
import struct
from typing import Any

_ALGOS = {"SHA-256": hashlib.sha256, "SHA-1": hashlib.sha1, "SHA-512": hashlib.sha512}


def make_friendly_puzzle(
    difficulty: int = 8,
    workers: int = 2,
    *,
    seed: int | None = 0,
    signature: str | None = None,
) -> dict[str, Any]:
    """Build a Friendly Captcha payload (compact `puzzle` form).

    `seed=None` draws fresh randomness; a fixed seed makes tests deterministic.
    The payload is accepted by `FriendlySolver._parse_puzzle`.
    """
    if seed is None:
        salt = secrets.token_bytes(16)
    else:
        salt = hashlib.sha256(struct.pack("<Q", seed & 0xFFFFFFFFFFFFFFFF)).digest()[:16]
    sig = signature if signature is not None else "test-" + hashlib.sha256(salt).hexdigest()[:16]
    return {"puzzle": f"{sig}.{difficulty}.{workers}.{salt.hex()}"}


def make_altcha_challenge(
    number: int,
    *,
    max_n: int | None = None,
    algorithm: str = "SHA-256",
    salt: str | None = None,
    hmac_secret: str = "test-secret",
) -> dict[str, Any]:
    """Build a solvable ALTCHA payload with a known solution `number`."""
    if number < 0:
        raise ValueError("number must be >= 0")
    max_n = max_n if max_n is not None else max(number * 2 + 10, 100)
    if number > max_n:
        raise ValueError(f"number ({number}) > max_n ({max_n})")
    if algorithm not in _ALGOS:
        raise ValueError(f"unsupported algorithm: {algorithm}")
    salt = salt if salt is not None else secrets.token_hex(8)
    hasher = _ALGOS[algorithm]
    challenge = hasher((salt + str(number)).encode("ascii")).hexdigest()
    # Real server signs with HMAC over `challenge` using a shared secret. For
    # tests we just hash; the solver does not validate the signature anyway.
    signature = hashlib.sha256((challenge + hmac_secret).encode()).hexdigest()
    return {
        "algorithm": algorithm,
        "challenge": challenge,
        "max": max_n,
        "salt": salt,
        "signature": signature,
    }
