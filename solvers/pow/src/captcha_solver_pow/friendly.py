"""Friendly Captcha PoW solver.

Protocol (as handed by this project's spec):
  server -> {signature, difficulty D, workers W, salt_base}
  client -> for each worker i in [0..W): find nonce n s.t.
            leading_zero_bits( sha256(salt_base || u32le(i) || u64le(n)) ) >= D

Token: base64 of {"solutions":[{"worker":i,"nonce":n},...],
                  "response_signature":..., "worker_count":W}.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar

from captcha_common import CaptchaType, SolverResult, Task
from captcha_common.errors import CaptchaError, TimeoutError as CaptchaTimeout, UnsolvableError
from captcha_solver_base import Solver, SolverContext, SolverInfo

from captcha_solver_pow._hash import leading_zero_bits

log = logging.getLogger(__name__)

# Byte layout of each worker's mining input. One place to edit if the server
# flips endianness or widens the fields.
_WORKER_IDX_FMT = "<I"           # u32 little-endian
_NONCE_FMT = "<Q"                # u64 little-endian
_NONCE_MAX = (1 << 64) - 1
_DEADLINE_CHECK_MASK = 0xFFF     # check wall clock every 4096 hashes

# Guardrails — a hostile payload shouldn't let us burn the box.
_MAX_WORKERS = 10_000
_MAX_DIFFICULTY = 256            # a sha256 digest is 256 bits


def _solve_worker(
    salt_base: bytes,
    worker_idx: int,
    difficulty: int,
    deadline_mono: float | None,
) -> int:
    """Mine one worker's nonce. Runs on a thread (sha256 drops the GIL)."""
    prefix = salt_base + struct.pack(_WORKER_IDX_FMT, worker_idx)
    pack_nonce = struct.Struct(_NONCE_FMT).pack
    sha = hashlib.sha256
    lzb = leading_zero_bits

    n = 0
    while n <= _NONCE_MAX:
        digest = sha(prefix + pack_nonce(n)).digest()
        if lzb(digest) >= difficulty:
            return n
        n += 1
        if (n & _DEADLINE_CHECK_MASK) == 0 and deadline_mono is not None:
            if time.monotonic() > deadline_mono:
                raise CaptchaTimeout(
                    "friendly: deadline exceeded while mining",
                    context={"worker": worker_idx, "nonce": n, "difficulty": difficulty},
                )
    raise UnsolvableError(
        "friendly: nonce space exhausted",
        context={"worker": worker_idx, "difficulty": difficulty},
    )


def _parse_puzzle(payload: dict[str, Any]) -> tuple[str, int, int, bytes]:
    """Accept compact `puzzle` string OR structured fields; return
    (signature, difficulty, workers, salt_base_bytes)."""
    if "puzzle" in payload:
        s = payload["puzzle"]
        if not isinstance(s, str):
            raise UnsolvableError("friendly: puzzle must be string", context={"got": type(s).__name__})
        parts = s.split(".")
        if len(parts) != 4:
            raise UnsolvableError("friendly: expected 4 dot-parts in puzzle", context={"parts": len(parts)})
        sig, diff_s, workers_s, salt_hex = parts
        try:
            diff = int(diff_s)
            workers = int(workers_s)
            salt_bytes = binascii.unhexlify(salt_hex)
        except (ValueError, binascii.Error) as exc:
            raise UnsolvableError("friendly: puzzle decode failed", context={"error": str(exc)}) from exc
        return sig, diff, workers, salt_bytes

    sig = payload.get("signature")
    diff = payload.get("difficulty")
    workers = payload.get("workers")
    salt = payload.get("salt_base")
    if not isinstance(sig, str) or not isinstance(diff, int) or not isinstance(workers, int):
        raise UnsolvableError(
            "friendly: missing/invalid fields",
            context={"keys": list(payload.keys())},
        )
    if isinstance(salt, str):
        try:
            salt_bytes = binascii.unhexlify(salt)
        except binascii.Error as exc:
            raise UnsolvableError("friendly: salt_base hex decode", context={"error": str(exc)}) from exc
    elif isinstance(salt, (bytes, bytearray)):
        salt_bytes = bytes(salt)
    else:
        raise UnsolvableError("friendly: salt_base missing or wrong type",
                              context={"got": type(salt).__name__})
    return sig, diff, workers, salt_bytes


class FriendlySolver(Solver):
    """SHA-256 PoW, parallel over workers. CPU-bound; scales with core count."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="friendly",
        types=(CaptchaType.FRIENDLY,),
        http=True,
        browser=False,
        version="0.1.0",
    )

    def __init__(self) -> None:
        super().__init__()
        self._pool: ThreadPoolExecutor | None = None

    async def prepare(self, ctx: SolverContext) -> None:
        # One pool per solver instance, sized to physical cores.
        max_workers = max(1, os.cpu_count() or 2)
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="friendly-pow",
        )

    async def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        signature, difficulty, workers, salt_base = _parse_puzzle(task.payload)

        if not (0 < workers <= _MAX_WORKERS):
            raise UnsolvableError("friendly: workers out of range",
                                  context={"workers": workers, "max": _MAX_WORKERS})
        if not (0 <= difficulty <= _MAX_DIFFICULTY):
            raise UnsolvableError("friendly: difficulty out of range",
                                  context={"difficulty": difficulty, "max": _MAX_DIFFICULTY})

        # Lazy pool spin-up for callers who skipped prepare() (tests, canary).
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=max(1, os.cpu_count() or 2),
                thread_name_prefix="friendly-pow",
            )

        mono_deadline = self._resolve_deadline(task.deadline_ts)

        t0 = time.perf_counter()
        loop = asyncio.get_running_loop()
        futures = [
            loop.run_in_executor(
                self._pool, _solve_worker, salt_base, i, difficulty, mono_deadline,
            )
            for i in range(workers)
        ]
        try:
            nonces = await asyncio.gather(*futures)
        except CaptchaError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CaptchaError("friendly: worker crashed",
                               context={"error": str(exc), "type": type(exc).__name__}) from exc
        took_ms = (time.perf_counter() - t0) * 1000.0

        solutions = [{"worker": i, "nonce": n} for i, n in enumerate(nonces)]
        token_obj = {
            "solutions": solutions,
            "response_signature": signature,
            "worker_count": workers,
        }
        token_b64 = base64.b64encode(
            json.dumps(token_obj, separators=(",", ":")).encode("ascii"),
        ).decode("ascii")

        log.info(
            "friendly solved: difficulty=%d workers=%d took_ms=%.1f",
            difficulty, workers, took_ms,
        )
        return SolverResult(
            token=token_b64,
            extra={
                "difficulty": difficulty,
                "workers": workers,
                "solutions": solutions,
                "signature": signature,
            },
            latency_ms=took_ms,
        )

    @staticmethod
    def _resolve_deadline(deadline_ts: float | None) -> float | None:
        """Wall-clock deadline -> monotonic deadline. Raises if already past."""
        if deadline_ts is None:
            return None
        budget = deadline_ts - time.time()
        if budget <= 0:
            raise CaptchaTimeout("friendly: already past deadline",
                                 context={"deadline_ts": deadline_ts})
        return time.monotonic() + budget
