"""ALTCHA PoW solver.

Protocol:
  server -> {algorithm, challenge (hex digest), max N, salt, signature (hmac)}
  client -> find N in [0..max] s.t. hex(algorithm(salt + str(N))) == challenge
  token  -> json.dumps({algorithm, challenge, number, salt, signature, took_ms})
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.domain.errors import UnsolvableError
from captcha.solvers import Solver, SolverContext, SolverInfo

log = logging.getLogger(__name__)

_SUPPORTED_ALGOS: dict[str, Callable[[bytes], Any]] = {
    "SHA-256": hashlib.sha256,
    "SHA-1": hashlib.sha1,
    "SHA-512": hashlib.sha512,
}

# ALTCHA tops out at ~1e6 in the wild; 1e10 keeps us safe from a malicious payload.
_MAX_N_CEILING = 10**10
_DEADLINE_CHECK_MASK = 0xFFFF


def _parse_challenge(payload: dict[str, Any]) -> tuple[str, str, int, str, str]:
    """Return (algorithm, challenge_hex, max_n, salt, signature)."""
    algo = payload.get("algorithm", "SHA-256")
    challenge = payload.get("challenge")
    # Some ALTCHA integrations use `maxnumber`; accept either key.
    max_n = payload.get("max", payload.get("maxnumber"))
    salt = payload.get("salt")
    signature = payload.get("signature", "")

    if algo not in _SUPPORTED_ALGOS:
        raise UnsolvableError(
            "altcha: unsupported algorithm", context={"algo": algo, "supported": sorted(_SUPPORTED_ALGOS)}
        )
    if not isinstance(challenge, str) or not challenge:
        raise UnsolvableError("altcha: challenge missing", context={"got": type(challenge).__name__})
    if not isinstance(salt, str):
        raise UnsolvableError("altcha: salt missing", context={"got": type(salt).__name__})
    if not isinstance(max_n, int):
        raise UnsolvableError("altcha: max missing or non-int", context={"got": type(max_n).__name__})
    if not isinstance(signature, str):
        raise UnsolvableError("altcha: signature must be string", context={"got": type(signature).__name__})
    return algo, challenge.lower(), max_n, salt, signature


class AltchaSolver(Solver):
    """Single-threaded linear scan — ALTCHA ranges are small and short-circuits fast."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="altcha",
        types=(CaptchaType.ALTCHA,),
        http=True,
        browser=False,
        version="0.1.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        algo, challenge, max_n, salt, signature = _parse_challenge(task.payload)
        hasher = _SUPPORTED_ALGOS[algo]

        if not (0 <= max_n <= _MAX_N_CEILING):
            raise UnsolvableError("altcha: max out of range", context={"max": max_n, "ceiling": _MAX_N_CEILING})

        mono_deadline = self._resolve_deadline(task.deadline_ts)

        t0 = time.perf_counter()
        salt_bytes = salt.encode("utf-8")
        found: int | None = None

        for n in range(max_n + 1):
            if hasher(salt_bytes + str(n).encode("ascii")).hexdigest() == challenge:
                found = n
                break
            if (n & _DEADLINE_CHECK_MASK) == 0 and mono_deadline is not None and time.monotonic() > mono_deadline:
                raise CaptchaTimeout(
                    "altcha: deadline exceeded",
                    context={"last_n": n, "max": max_n},
                )

        if found is None:
            raise UnsolvableError(
                "altcha: no number in [0..max] matches challenge",
                context={"max": max_n, "algorithm": algo},
            )

        took_ms = (time.perf_counter() - t0) * 1000.0
        payload_obj = {
            "algorithm": algo,
            "challenge": challenge,
            "number": found,
            "salt": salt,
            "signature": signature,
            "took_ms": int(took_ms),
        }
        token = json.dumps(payload_obj, separators=(",", ":"))

        log.info("altcha solved: n=%d max=%d took_ms=%.1f", found, max_n, took_ms)
        return SolverResult(
            token=token,
            extra=payload_obj,
            latency_ms=took_ms,
        )

    @staticmethod
    def _resolve_deadline(deadline_ts: float | None) -> float | None:
        if deadline_ts is None:
            return None
        budget = deadline_ts - time.time()
        if budget <= 0:
            raise CaptchaTimeout("altcha: already past deadline", context={"deadline_ts": deadline_ts})
        return time.monotonic() + budget
