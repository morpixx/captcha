from __future__ import annotations

from typing import Any, Final
from uuid import UUID, uuid4

import msgspec

from captcha.domain.enums import CaptchaType, Priority, SolveMode, TaskStatus
from captcha.domain.errors import UnsolvableError

#: How many times one task may be moved between lanes. One hop: an HTTP solver
#: may ask for the browser lane once; a second request is a dead end.
MAX_LANE_HOPS: Final[int] = 1


class Task(msgspec.Struct, frozen=True, kw_only=True):
    """Immutable task definition; whatever a solver needs to produce a result.

    `payload` is per-type: a solver knows how to decode its own schema.
    See docs/protocol.md for the per-type payload shape.
    """

    id: UUID
    type: CaptchaType
    payload: dict[str, Any]
    priority: Priority = Priority.NORMAL
    preferred_mode: SolveMode = SolveMode.AUTO
    sitekey: str | None = None
    site_url: str | None = None
    proxy: str | None = None
    user_agent: str | None = None
    language: str | None = None
    callback_url: str | None = None
    created_at: float = 0.0
    deadline_ts: float | None = None
    client_id: str | None = None
    attempt: int = 0


class TaskEnvelope(msgspec.Struct, kw_only=True):
    """Mutable wrapper for queue/DB. Status transitions live here, not in Task."""

    task: Task
    status: TaskStatus = TaskStatus.PENDING
    result: SolverResult | None = None
    error: dict[str, Any] | None = None
    started_at: float | None = None
    finished_at: float | None = None
    worker_id: str | None = None
    retries: int = 0
    #: Lane moves already spent on this task. Capped by `MAX_LANE_HOPS`; the
    #: value travels with the whole envelope through msgpack, requeue and DLQ.
    lane_hops: int = 0

    def escalated(self) -> TaskEnvelope:
        """Return a copy that spent one lane hop (http -> browser).

        Raises `UnsolvableError` when the envelope already used its single hop:
        a second `NeedsBrowserError` is structural, not a retry.
        """
        if self.lane_hops >= MAX_LANE_HOPS:
            raise UnsolvableError(
                "lane escalation budget exhausted",
                context={
                    "type": self.task.type.value,
                    "lane_hops": self.lane_hops,
                },
            )
        return msgspec.structs.replace(self, lane_hops=self.lane_hops + 1)


class SolverResult(msgspec.Struct, kw_only=True):
    """Solver return. Shape depends on captcha type.

    Common fields: `token` (most text-token results), `coords` (click/drag),
    `offset` (slider), `angle` (rotate), `bbox` (bbox), `indices` (grid).
    """

    token: str | None = None
    coords: list[tuple[float, float]] | None = None
    offset: float | None = None
    angle: float | None = None
    bbox: tuple[float, float, float, float] | None = None
    indices: list[int] | None = None
    text: str | None = None
    extra: dict[str, Any] | None = None
    solve_mode: SolveMode = SolveMode.HTTP
    latency_ms: float = 0.0


def new_task_id() -> UUID:
    return uuid4()
