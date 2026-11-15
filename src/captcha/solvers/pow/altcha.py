"""ALTCHA proof-of-work solver (altcha-lib v1 wire).

Protocol:
  server -> challenge {algorithm, challenge, maxnumber, salt, signature}
  client -> find N in [0..maxnumber] with hex(alg(salt + str(N))) == challenge
  token  -> base64(json {algorithm, challenge, number, salt, signature})

The salt may carry a query string (`...?expires=...&`). It is part of the hash
preimage and altcha-lib rejects a stale `expires`, so we check it before mining.

v2/v3 (PBKDF2/Scrypt/Argon2 key derivation against `parameters.keyPrefix`) is a
different payload shape. It is rejected explicitly rather than mined against the
v1 preimage, which could never verify.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Callable
from typing import Any, ClassVar
from urllib.parse import parse_qsl

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError, UnsolvableError
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.solvers import Solver, SolverContext, SolverInfo

_SUPPORTED_ALGOS: dict[str, Callable[[bytes], Any]] = {
    "SHA-1": hashlib.sha1,
    "SHA-256": hashlib.sha256,
    "SHA-512": hashlib.sha512,
}

# altcha-lib's own default is 1e6. A hostile payload must not turn into an
# unbounded loop, so anything beyond 1e9 is refused outright and the deadline
# covers the rest.
_DEFAULT_MAX_N = 1_000_000
_MAX_N_CEILING = 10**9
_DEFAULT_BUDGET_SEC = 30.0
_DEADLINE_CHECK_MASK = 0xFFFF


def _salt_params(salt: str) -> dict[str, str]:
    if "?" not in salt:
        return {}
    query = salt.split("?", 1)[1].rstrip("&")
    return dict(parse_qsl(query, keep_blank_values=True))


def _check_expiry(salt: str) -> None:
    params = _salt_params(salt)
    raw = params.get("expires") or params.get("expire")
    if not raw:
        return
    try:
        expires = int(raw)
    except ValueError as exc:
        raise ChallengeExpiredError("altcha: salt carries a malformed expiry", context={"expires": raw}) from exc
    if expires <= time.time():
        raise ChallengeExpiredError("altcha: challenge expired", context={"expires": expires})


def _parse_challenge(payload: dict[str, Any]) -> tuple[str, str, int, str, str]:
    """Return (algorithm, challenge_hex, max_n, salt, signature)."""
    if "parameters" in payload or isinstance(payload.get("challenge"), dict):
        raise UnsolvableError(
            "altcha: v2/v3 payload (KDF parameters) is not supported; expected altcha-lib v1",
            context={"keys": sorted(payload)},
        )
    algo = payload.get("algorithm", "SHA-256")
    challenge = payload.get("challenge")
    max_n = payload.get("maxnumber", payload.get("max", _DEFAULT_MAX_N))
    salt = payload.get("salt")
    signature = payload.get("signature", "")

    if not isinstance(algo, str) or algo not in _SUPPORTED_ALGOS:
        raise UnsolvableError(
            "altcha: unsupported algorithm",
            context={"algo": algo, "supported": sorted(_SUPPORTED_ALGOS)},
        )
    if not isinstance(challenge, str) or not challenge:
        raise UnsolvableError("altcha: challenge missing", context={"got": type(challenge).__name__})
    if not isinstance(salt, str):
        raise UnsolvableError("altcha: salt missing", context={"got": type(salt).__name__})
    if isinstance(max_n, bool) or not isinstance(max_n, int):
        raise UnsolvableError("altcha: max missing or non-int", context={"got": type(max_n).__name__})
    if not isinstance(signature, str):
        raise UnsolvableError("altcha: signature must be string", context={"got": type(signature).__name__})

    digest_hex_len = _SUPPORTED_ALGOS[algo]().digest_size * 2
    challenge = challenge.lower()
    if len(challenge) != digest_hex_len:
        raise UnsolvableError(
            "altcha: challenge length does not match the algorithm",
            context={"algo": algo, "length": len(challenge), "expected": digest_hex_len},
        )
    try:
        int(challenge, 16)
    except ValueError as exc:
        raise UnsolvableError("altcha: challenge is not hex", context={"algo": algo}) from exc
    return algo, challenge, max_n, salt, signature


def _monotonic_deadline(deadline_ts: float | None) -> float:
    remaining = _DEFAULT_BUDGET_SEC if deadline_ts is None else deadline_ts - time.time()
    if remaining <= 0:
        raise CaptchaTimeout("altcha: task deadline already passed", context={"deadline_ts": deadline_ts})
    return time.monotonic() + remaining


def _scan(hasher: Callable[[bytes], Any], salt_bytes: bytes, challenge: str, max_n: int, deadline: float) -> int:
    for n in range(max_n + 1):
        if hasher(salt_bytes + str(n).encode("ascii")).hexdigest() == challenge:
            return n
        if (n & _DEADLINE_CHECK_MASK) == 0 and time.monotonic() > deadline:
            raise CaptchaTimeout("altcha: deadline exceeded", context={"last_n": n, "max": max_n})
    raise UnsolvableError(
        "altcha: no number in [0..max] matches challenge",
        context={"max": max_n},
    )


class AltchaSolver(Solver):
    """Single-threaded linear scan — ALTCHA ranges are small and short-circuit fast."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="altcha",
        types=(CaptchaType.ALTCHA,),
        http=True,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        payload = task.payload or {}
        algo, challenge, max_n, salt, signature = _parse_challenge(payload)
        if not 0 <= max_n <= _MAX_N_CEILING:
            raise UnsolvableError(
                "altcha: max out of range",
                context={"max": max_n, "ceiling": _MAX_N_CEILING},
            )
        _check_expiry(salt)

        deadline = _monotonic_deadline(task.deadline_ts)
        t0 = time.perf_counter()
        found = await asyncio.to_thread(_scan, _SUPPORTED_ALGOS[algo], salt.encode("utf-8"), challenge, max_n, deadline)
        took_ms = (time.perf_counter() - t0) * 1000.0

        solution = {
            "algorithm": algo,
            "challenge": challenge,
            "number": found,
            "salt": salt,
            "signature": signature,
        }
        token = base64.b64encode(json.dumps(solution, separators=(",", ":")).encode("utf-8")).decode("ascii")

        return SolverResult(
            token=token,
            extra={"algorithm": algo, "number": found, "took_ms": round(took_ms, 1)},
            latency_ms=took_ms,
        )
