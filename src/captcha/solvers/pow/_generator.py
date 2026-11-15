"""Deterministic PoW challenge generators. Test and canary only; never registered.

Friendly Captcha puzzles follow the friendly-pow v1 wire: a 32..64 byte buffer
(timestamp, account, app, version, expiry, solution count, difficulty, nonce),
base64-encoded and prefixed with the server signature.

ALTCHA challenges follow altcha-lib v1: `{algorithm, challenge, maxnumber, salt,
signature}` where `challenge = hex(hash(salt + number))`. The solver does not
verify the signature (the site's secret never leaves the site).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import struct
import time
from typing import Any

_ALGOS = {"SHA-1": hashlib.sha1, "SHA-256": hashlib.sha256, "SHA-512": hashlib.sha512}

_FRIENDLY_VERSION = 1
_FRIENDLY_CORE_BYTES = 32
_FRIENDLY_MAX_BYTES = 64


def _friendly_buffer(
    *,
    timestamp: int,
    difficulty: int,
    solutions: int,
    expiry: int,
    seed: int,
    extra_data: bytes,
) -> bytes:
    if not 1 <= solutions <= 255:
        raise ValueError("solutions must be in [1, 255]")
    if not 0 <= difficulty <= 255:
        raise ValueError("difficulty must be a byte")
    if not 0 <= expiry <= 255:
        raise ValueError("expiry must be a byte")
    if len(extra_data) > _FRIENDLY_MAX_BYTES - _FRIENDLY_CORE_BYTES or len(extra_data) % 32:
        raise ValueError("extra_data must be 0 or 32 bytes")
    nonce = hashlib.sha256(struct.pack("<Q", seed & 0xFFFFFFFFFFFFFFFF)).digest()[:8]
    # timestamp | account | app | version | expiry | solutions | difficulty | reserved
    core = struct.pack(">IIIBBBBQ", timestamp, 0, 0, _FRIENDLY_VERSION, expiry, solutions, difficulty, 0)
    return core + nonce + extra_data


def make_friendly_puzzle(
    difficulty: int = 40,
    solutions: int = 4,
    *,
    seed: int = 0,
    signature: str | None = None,
    timestamp: int | None = None,
    expiry: int = 0,
    extra_data: bytes = b"",
) -> dict[str, Any]:
    """Build a Friendly Captcha v1 payload (`puzzle` = `<signature>.<base64 buffer>`).

    `expiry` is the raw byte: seconds of validity are `expiry * 300`, 0 = never.
    The signature is only meaningful to the site's server; here it is a stable
    placeholder so fixtures are reproducible.
    """
    ts = int(time.time()) if timestamp is None else int(timestamp)
    buf = _friendly_buffer(
        timestamp=ts,
        difficulty=difficulty,
        solutions=solutions,
        expiry=expiry,
        seed=seed,
        extra_data=extra_data,
    )
    sig = signature if signature is not None else "test-" + hashlib.sha256(buf).hexdigest()[:16]
    return {"puzzle": f"{sig}.{base64.b64encode(buf).decode('ascii')}"}


def make_altcha_challenge(
    number: int,
    *,
    max_n: int | None = None,
    algorithm: str = "SHA-256",
    salt: str | None = None,
    hmac_secret: str = "test-secret",
    expires_at: int | None = None,
) -> dict[str, Any]:
    """Build a solvable ALTCHA v1 payload with a known solution `number`.

    Mirrors altcha-lib `createChallenge`: the salt gets an `expires` query
    parameter when `expires_at` is set and always ends with `&`, which is part
    of the hash preimage.
    """
    if number < 0:
        raise ValueError("number must be >= 0")
    max_n = max_n if max_n is not None else max(number * 2 + 10, 100)
    if number > max_n:
        raise ValueError(f"number ({number}) > max_n ({max_n})")
    if algorithm not in _ALGOS:
        raise ValueError(f"unsupported algorithm: {algorithm}")
    hasher = _ALGOS[algorithm]
    base = salt if salt is not None else secrets.token_hex(8)
    if expires_at is not None:
        base = f"{base}?expires={int(expires_at)}"
    if not base.endswith("&"):
        base = base + "&"
    challenge = hasher((base + str(number)).encode("utf-8")).hexdigest()
    signature = hashlib.sha256((challenge + hmac_secret).encode("utf-8")).hexdigest()
    return {
        "algorithm": algorithm,
        "challenge": challenge,
        "maxnumber": max_n,
        "salt": base,
        "signature": signature,
    }
