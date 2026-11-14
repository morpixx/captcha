from __future__ import annotations

from typing import TYPE_CHECKING, Any

import msgspec
import structlog
from captcha.domain.task import TaskEnvelope

from captcha.domain.lanes import pick_lane
from .keys import (
    Lane,
    consumer_group,
    dead_letter_stream,
    stream_name,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

_log = structlog.get_logger(__name__)

_FIELD = b"data"
_FIELD_STR = "data"
_REASON_FIELD = b"reason"

_envelope_encoder = msgspec.msgpack.Encoder()
_envelope_decoder = msgspec.msgpack.Decoder(TaskEnvelope)


def _encode(envelope: TaskEnvelope) -> bytes:
    return _envelope_encoder.encode(envelope)


def _decode(raw: bytes) -> TaskEnvelope:
    return _envelope_decoder.decode(raw)


def _extract_payload(fields: dict[Any, Any]) -> bytes:
    # Redis-py returns bytes keys by default; be defensive about str keys too.
    raw = fields.get(_FIELD)
    if raw is None:
        raw = fields.get(_FIELD_STR)
    if raw is None:
        raise ValueError("stream entry missing 'data' field")
    return raw if isinstance(raw, (bytes, bytearray)) else bytes(raw, "utf-8")


class Producer:
    """XADD side of the lane streams.

    Picks the lane with `dispatch.pick_lane` when the caller doesn't force one,
    then appends a msgpack-encoded `TaskEnvelope` with `MAXLEN ~ <cap>` trimming
    so a runaway producer can't blow the keyspace.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        prefix: str = "captcha",
        maxlen: int = 100_000,
        approximate: bool = True,
    ) -> None:
        self._r = redis
        self._prefix = prefix
        self._maxlen = maxlen
        self._approximate = approximate

    async def publish(
        self,
        envelope: TaskEnvelope,
        *,
        lane: Lane | None = None,
    ) -> str:
        chosen: Lane = lane if lane is not None else pick_lane(envelope.task)
        stream = stream_name(self._prefix, chosen)
        payload = _encode(envelope)
        message_id = await self._r.xadd(
            stream,
            {_FIELD: payload},
            maxlen=self._maxlen,
            approximate=self._approximate,
        )
        _log.debug(
            "task.published",
            task_id=str(envelope.task.id),
            lane=chosen,
            stream=stream,
            msg_id=message_id,
        )
        return message_id.decode() if isinstance(message_id, bytes) else str(message_id)

    async def publish_dead_letter(
        self,
        envelope: TaskEnvelope,
        reason: str,
    ) -> str:
        stream = dead_letter_stream(self._prefix)
        payload = _encode(envelope)
        message_id = await self._r.xadd(
            stream,
            {_FIELD: payload, _REASON_FIELD: reason.encode("utf-8")},
            maxlen=self._maxlen,
            approximate=self._approximate,
        )
        return message_id.decode() if isinstance(message_id, bytes) else str(message_id)


class Consumer:
    """XREADGROUP + XCLAIM side of the lane streams.

    Read loop:
        1. `claim_stale()` — reclaim entries idle longer than `min_idle_ms`
           (crashed worker, blocked network); their delivery counter bumps.
        2. `read()` — XREADGROUP new entries with BLOCK + COUNT.

    Retry policy lives in `fail()`: ack then re-XADD with `retries += 1`, or
    route to the dead-letter stream once `retries >= max_retries`.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        lane: Lane,
        consumer_name: str,
        prefix: str = "captcha",
        block_ms: int = 5_000,
        count: int = 16,
        max_retries: int = 3,
        min_idle_ms: int = 60_000,
        maxlen: int = 100_000,
    ) -> None:
        self._r = redis
        self._lane: Lane = lane
        self._consumer = consumer_name
        self._prefix = prefix
        self._block_ms = block_ms
        self._count = count
        self._max_retries = max_retries
        self._min_idle_ms = min_idle_ms
        self._maxlen = maxlen
        self._stream = stream_name(prefix, lane)
        self._group = consumer_group(lane)
        self._dlq = dead_letter_stream(prefix)
        self._autoclaim_cursor: bytes | str = "0-0"

    @property
    def lane(self) -> Lane:
        return self._lane

    @property
    def stream(self) -> str:
        return self._stream

    @property
    def group(self) -> str:
        return self._group

    async def ensure_group(self) -> None:
        """Idempotently create the consumer group + stream. Swallow BUSYGROUP."""
        try:
            await self._r.xgroup_create(
                name=self._stream,
                groupname=self._group,
                id="$",
                mkstream=True,
            )
            _log.info("group.created", stream=self._stream, group=self._group)
        except Exception as exc:  # redis.exceptions.ResponseError on BUSYGROUP
            if "BUSYGROUP" in str(exc):
                return
            raise

    async def read(self) -> list[tuple[str, TaskEnvelope]]:
        """One pass: reclaim stale, then XREADGROUP. May return [] on timeout."""
        out: list[tuple[str, TaskEnvelope]] = []
        out.extend(await self.claim_stale())
        resp = await self._r.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={self._stream: ">"},
            count=self._count,
            block=self._block_ms,
        )
        if not resp:
            return out
        # resp = [(stream_name_bytes, [(msg_id_bytes, {field: value}), ...])]
        for _stream, entries in resp:
            for msg_id, fields in entries:
                try:
                    envelope = _decode(_extract_payload(fields))
                except Exception as exc:
                    _log.error(
                        "envelope.decode_failed",
                        msg_id=msg_id,
                        stream=self._stream,
                        error=str(exc),
                    )
                    # Poison pill: ack and shove the raw bytes into DLQ.
                    await self._r.xack(self._stream, self._group, msg_id)
                    await self._r.xadd(
                        self._dlq,
                        {
                            _FIELD: _extract_payload(fields) if fields else b"",
                            _REASON_FIELD: f"decode_failed:{exc}".encode("utf-8"),
                        },
                        maxlen=self._maxlen,
                        approximate=True,
                    )
                    continue
                mid = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
                out.append((mid, envelope))
        return out

    async def claim_stale(self) -> list[tuple[str, TaskEnvelope]]:
        """XAUTOCLAIM a chunk of entries past the idle threshold onto us."""
        # redis-py: xautoclaim(name, groupname, consumername, min_idle_time, start_id, count, justid)
        try:
            next_cursor, claimed, _deleted = await self._r.xautoclaim(
                name=self._stream,
                groupname=self._group,
                consumername=self._consumer,
                min_idle_time=self._min_idle_ms,
                start_id=self._autoclaim_cursor,
                count=self._count,
            )
        except Exception as exc:
            _log.warning("xautoclaim.failed", error=str(exc))
            return []
        self._autoclaim_cursor = next_cursor if next_cursor else "0-0"
        out: list[tuple[str, TaskEnvelope]] = []
        for msg_id, fields in claimed:
            try:
                envelope = _decode(_extract_payload(fields))
            except Exception as exc:
                _log.error("reclaim.decode_failed", msg_id=msg_id, error=str(exc))
                await self._r.xack(self._stream, self._group, msg_id)
                continue
            mid = msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id)
            out.append((mid, envelope))
        return out

    async def ack(self, message_id: str) -> None:
        await self._r.xack(self._stream, self._group, message_id)

    async def fail(
        self,
        message_id: str,
        envelope: TaskEnvelope,
        reason: str,
    ) -> bool:
        """Mark one attempt as failed. Returns True if the task was dead-lettered.

        Ack the original entry either way (we've taken responsibility for it);
        then either re-enqueue a fresh copy with `retries += 1` or write it to
        the DLQ for a human/janitor to look at.
        """
        next_retries = envelope.retries + 1
        await self._r.xack(self._stream, self._group, message_id)
        if next_retries >= self._max_retries:
            await self._r.xadd(
                self._dlq,
                {
                    _FIELD: _encode(envelope),
                    _REASON_FIELD: reason.encode("utf-8"),
                },
                maxlen=self._maxlen,
                approximate=True,
            )
            _log.warning(
                "task.dead_lettered",
                task_id=str(envelope.task.id),
                retries=envelope.retries,
                reason=reason,
            )
            return True
        bumped = msgspec.structs.replace(envelope, retries=next_retries)
        await self._r.xadd(
            self._stream,
            {_FIELD: _encode(bumped)},
            maxlen=self._maxlen,
            approximate=True,
        )
        _log.info(
            "task.requeued",
            task_id=str(envelope.task.id),
            attempt=next_retries,
            reason=reason,
        )
        return False

    async def pending_summary(self) -> dict[str, Any]:
        """Thin wrapper over XPENDING for health endpoints."""
        return await self._r.xpending(self._stream, self._group)
