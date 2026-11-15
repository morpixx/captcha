"""Friendly Captcha v1 proof-of-work solver.

Wire (FriendlyCaptcha/friendly-pow):
  server -> "<signature>.<base64 puzzle buffer>"
  buffer -> u32be timestamp | u32be account | u32be app | u8 version | u8 expiry |
            u8 solutions | u8 difficulty | 8 reserved | 8 nonce | [32 bytes data]
  client -> zero-pad the buffer to 128 bytes, put a candidate into the last 8
            bytes, keep it while blake2b-256(buffer)[:4] as u32le is below
            T = floor(2 ** ((255.999 - difficulty) / 8))
  token  -> "<signature>.<b64 buffer>.<b64 solutions>.<b64 diagnostics>"

v2 (session signals, pointer/keyboard telemetry) is a browser protocol and is out
of scope here - see docs/reverse-2026.md section 12.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import time
from dataclasses import dataclass
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import ChallengeExpiredError, UnsolvableError
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.solvers import Solver, SolverContext, SolverInfo
from captcha.solvers.pow._hash import difficulty_threshold, hash_meets_threshold

_CORE_BYTES = 32
_MAX_BYTES = 64
_HASH_INPUT_BYTES = 128
_SOLUTION_BYTES = 8
_SOLUTION_OFFSET = _HASH_INPUT_BYTES - _SOLUTION_BYTES
_VERSION = 1
_EXPIRY_UNIT_SEC = 300
_UINT16_MAX = 0xFFFF
_DEADLINE_CHECK_MASK = 0xFF
# Worker-http caps a task at ~30s; without a task deadline we still refuse to mine
# forever on a hostile difficulty byte.
_DEFAULT_BUDGET_SEC = 30.0
_SOLVER_ID = 0  # 0 = unspecified, 1 = JS fallback, 2 = WASM32 (server-side only).


@dataclass(frozen=True, slots=True)
class Puzzle:
    """Decoded friendly-pow puzzle buffer."""

    signature: str
    buffer: bytes
    timestamp: int
    expires_in: int
    solutions: int
    difficulty: int


def _b64decode(raw: str, field: str) -> bytes:
    text = raw.strip()
    padded = text + "=" * ((-len(text)) % 4)
    try:
        return base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        pass
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise UnsolvableError(f"friendly: {field} is not valid base64", context={"error": str(exc)}) from exc


def _split_puzzle(payload: dict[str, Any]) -> tuple[str, str]:
    """Return (signature, base64 puzzle buffer) from either wire shape."""
    raw = payload.get("puzzle")
    if isinstance(raw, str) and raw:
        parts = raw.split(".")
        if len(parts) == 2 and all(parts):
            return parts[0], parts[1]
        if len(parts) == 1:
            signature = payload.get("signature")
            if isinstance(signature, str) and signature:
                return signature, parts[0]
        raise UnsolvableError(
            "friendly: puzzle must be '<signature>.<base64 buffer>'",
            context={"dot_parts": len(parts)},
        )
    encoded = payload.get("puzzle_buffer") or payload.get("puzzleBuffer")
    signature = payload.get("signature")
    if isinstance(encoded, str) and encoded and isinstance(signature, str) and signature:
        return signature, encoded
    raise UnsolvableError(
        "friendly: payload missing 'puzzle' (or 'signature' + 'puzzle_buffer')",
        context={"keys": sorted(payload)},
    )


def parse_puzzle(payload: dict[str, Any]) -> Puzzle:
    """Decode and validate a friendly-pow v1 puzzle. Raises on wire drift."""
    signature, encoded = _split_puzzle(payload)
    buf = _b64decode(encoded, "puzzle buffer")
    if not _CORE_BYTES <= len(buf) <= _MAX_BYTES:
        raise UnsolvableError(
            "friendly: puzzle buffer must be 32..64 bytes",
            context={"length": len(buf)},
        )
    version = buf[12]
    if version != _VERSION:
        raise UnsolvableError("friendly: unsupported puzzle version", context={"version": version})
    solutions = buf[14]
    if solutions == 0:
        raise UnsolvableError("friendly: puzzle asks for zero solutions")
    timestamp = int.from_bytes(buf[0:4], "big")
    expires_in = buf[13] * _EXPIRY_UNIT_SEC
    if expires_in and time.time() > timestamp + expires_in:
        raise ChallengeExpiredError(
            "friendly: puzzle expired",
            context={"timestamp": timestamp, "expires_in": expires_in},
        )
    return Puzzle(
        signature=signature,
        buffer=buf,
        timestamp=timestamp,
        expires_in=expires_in,
        solutions=solutions,
        difficulty=buf[15],
    )


def _monotonic_deadline(deadline_ts: float | None) -> float:
    remaining = _DEFAULT_BUDGET_SEC if deadline_ts is None else deadline_ts - time.time()
    if remaining <= 0:
        raise CaptchaTimeout("friendly: task deadline already passed", context={"deadline_ts": deadline_ts})
    return time.monotonic() + remaining


def _mine(buf: bytes, count: int, difficulty: int, deadline: float) -> list[int]:
    """Find `count` distinct 8-byte solutions. CPU-bound; runs in a worker thread."""
    threshold = difficulty_threshold(difficulty)
    base = buf.ljust(_HASH_INPUT_BYTES, b"\x00")[:_SOLUTION_OFFSET]
    blake = hashlib.blake2b
    found: list[int] = []
    candidate = 0
    while len(found) < count:
        digest = blake(base + candidate.to_bytes(_SOLUTION_BYTES, "big"), digest_size=32).digest()
        if hash_meets_threshold(digest, threshold):
            found.append(candidate)
        candidate += 1
        if (candidate & _DEADLINE_CHECK_MASK) == 0 and time.monotonic() > deadline:
            raise CaptchaTimeout(
                "friendly: deadline exceeded while mining",
                context={"found": len(found), "needed": count, "difficulty": difficulty},
            )
    return found


def _token(puzzle: Puzzle, nonces: list[int], took_sec: int) -> str:
    solutions = b"".join(n.to_bytes(_SOLUTION_BYTES, "big") for n in nonces)
    diagnostics = bytes((_SOLVER_ID, (took_sec >> 8) & 0xFF, took_sec & 0xFF))
    parts = (
        puzzle.signature,
        base64.b64encode(puzzle.buffer).decode("ascii"),
        base64.b64encode(solutions).decode("ascii"),
        base64.b64encode(diagnostics).decode("ascii"),
    )
    return ".".join(parts)


class FriendlySolver(Solver):
    """blake2b-256 PoW over the friendly-pow v1 puzzle buffer. Pure CPU."""

    info: ClassVar[SolverInfo] = SolverInfo(
        name="friendly",
        types=(CaptchaType.FRIENDLY,),
        http=True,
        version="0.2.0",
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        puzzle = parse_puzzle(task.payload or {})
        deadline = _monotonic_deadline(task.deadline_ts)

        t0 = time.perf_counter()
        nonces = await asyncio.to_thread(_mine, puzzle.buffer, puzzle.solutions, puzzle.difficulty, deadline)
        took_ms = (time.perf_counter() - t0) * 1000.0
        token = _token(puzzle, nonces, min(int(took_ms / 1000.0), _UINT16_MAX))

        return SolverResult(
            token=token,
            extra={
                "difficulty": puzzle.difficulty,
                "solutions": puzzle.solutions,
                "took_ms": round(took_ms, 1),
            },
            latency_ms=took_ms,
        )
