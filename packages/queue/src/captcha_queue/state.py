from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import msgspec
from captcha_common.enums import TaskStatus
from captcha_common.types import SolverResult, Task, TaskEnvelope

from .keys import task_hash_key

if TYPE_CHECKING:
    from redis.asyncio import Redis

_TTL_DEFAULT = 24 * 60 * 60  # 24h — matches 2captcha's client polling window

_task_encoder = msgspec.msgpack.Encoder()
_task_decoder = msgspec.msgpack.Decoder(Task)

_result_encoder = msgspec.msgpack.Encoder()
_result_decoder = msgspec.msgpack.Decoder(SolverResult)

_dict_encoder = msgspec.msgpack.Encoder()
_dict_decoder = msgspec.msgpack.Decoder(dict)


def _f_task() -> bytes:
    return b"task"


def _f_status() -> bytes:
    return b"status"


def _f_result() -> bytes:
    return b"result"


def _f_error() -> bytes:
    return b"error"


def _f_started() -> bytes:
    return b"started_at"


def _f_finished() -> bytes:
    return b"finished_at"


def _f_worker() -> bytes:
    return b"worker_id"


def _f_retries() -> bytes:
    return b"retries"


def _as_bytes(v: Any) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        return v.encode("utf-8")
    return str(v).encode("utf-8")


def _get(h: dict[Any, Any], key: bytes) -> Any | None:
    v = h.get(key)
    if v is None:
        v = h.get(key.decode("utf-8"))
    return v


class TaskStateStore:
    """Hash-per-task state, msgpack bodies, 24h TTL.

    The gateway `set_pending()`s on enqueue; the worker transitions through
    `set_processing` → `set_ready` / `set_failed`. `get()` is what the
    public `getTaskResult` endpoint calls on every client poll.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "captcha",
        ttl_seconds: int = _TTL_DEFAULT,
    ) -> None:
        self._r = redis
        self._prefix = prefix
        self._ttl = ttl_seconds

    def _key(self, task_id: UUID | str) -> str:
        return task_hash_key(self._prefix, task_id)

    async def set_pending(self, envelope: TaskEnvelope) -> None:
        """Seed the hash from an envelope the gateway just received."""
        key = self._key(envelope.task.id)
        mapping: dict[bytes, bytes] = {
            _f_task(): _task_encoder.encode(envelope.task),
            _f_status(): TaskStatus.PENDING.value.encode("utf-8"),
            _f_retries(): str(envelope.retries).encode("utf-8"),
        }
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, self._ttl)
            await pipe.execute()

    async def set_processing(
        self,
        task_id: UUID | str,
        worker_id: str,
        *,
        started_at: float,
    ) -> None:
        key = self._key(task_id)
        mapping: dict[bytes, bytes] = {
            _f_status(): TaskStatus.PROCESSING.value.encode("utf-8"),
            _f_worker(): worker_id.encode("utf-8"),
            _f_started(): repr(started_at).encode("utf-8"),
        }
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, self._ttl)
            await pipe.execute()

    async def set_ready(
        self,
        task_id: UUID | str,
        result: SolverResult,
        *,
        finished_at: float,
    ) -> None:
        key = self._key(task_id)
        mapping: dict[bytes, bytes] = {
            _f_status(): TaskStatus.READY.value.encode("utf-8"),
            _f_result(): _result_encoder.encode(result),
            _f_finished(): repr(finished_at).encode("utf-8"),
        }
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, self._ttl)
            await pipe.execute()

    async def set_failed(
        self,
        task_id: UUID | str,
        error: dict[str, Any] | str,
        *,
        finished_at: float,
    ) -> None:
        key = self._key(task_id)
        err_dict: dict[str, Any] = (
            {"code": "ERR_UNKNOWN", "message": error} if isinstance(error, str) else error
        )
        mapping: dict[bytes, bytes] = {
            _f_status(): TaskStatus.FAILED.value.encode("utf-8"),
            _f_error(): _dict_encoder.encode(err_dict),
            _f_finished(): repr(finished_at).encode("utf-8"),
        }
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, self._ttl)
            await pipe.execute()

    async def bump_retries(self, task_id: UUID | str) -> int:
        key = self._key(task_id)
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.hincrby(key, _f_retries(), 1)
            pipe.expire(key, self._ttl)
            retries, _ = await pipe.execute()
        return int(retries)

    async def get(self, task_id: UUID | str) -> TaskEnvelope | None:
        key = self._key(task_id)
        raw = await self._r.hgetall(key)
        if not raw:
            return None

        task_bytes = _get(raw, _f_task())
        if not task_bytes:
            # Hash exists but lost its task body (TTL race, partial write).
            return None
        task = _task_decoder.decode(_as_bytes(task_bytes))

        status_raw = _get(raw, _f_status())
        status = (
            TaskStatus(status_raw.decode() if isinstance(status_raw, bytes) else status_raw)
            if status_raw
            else TaskStatus.PENDING
        )

        result: SolverResult | None = None
        result_bytes = _get(raw, _f_result())
        if result_bytes:
            result = _result_decoder.decode(_as_bytes(result_bytes))

        error: dict[str, Any] | None = None
        error_bytes = _get(raw, _f_error())
        if error_bytes:
            error = _dict_decoder.decode(_as_bytes(error_bytes))

        started_at = _parse_float(_get(raw, _f_started()))
        finished_at = _parse_float(_get(raw, _f_finished()))

        worker_raw = _get(raw, _f_worker())
        worker_id = (
            worker_raw.decode("utf-8") if isinstance(worker_raw, bytes) else worker_raw
        )

        retries_raw = _get(raw, _f_retries())
        retries = int(retries_raw) if retries_raw is not None else 0

        return TaskEnvelope(
            task=task,
            status=status,
            result=result,
            error=error,
            started_at=started_at,
            finished_at=finished_at,
            worker_id=worker_id,
            retries=retries,
        )

    async def delete(self, task_id: UUID | str) -> None:
        await self._r.delete(self._key(task_id))

    async def exists(self, task_id: UUID | str) -> bool:
        return bool(await self._r.exists(self._key(task_id)))


def _parse_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
