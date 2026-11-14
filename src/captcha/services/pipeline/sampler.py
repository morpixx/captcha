"""Pulls recent Task rows per type and dumps them to S3 for human labelling.

One Sampler per process. `run_once()` samples every configured type and
returns a `SampleReport` for logging / alerting."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.adapters.storage import Task
from captcha.adapters.storage.engine import async_session

from captcha.services.pipeline.s3client import S3Writer

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class SampleReport:
    started_at: float
    finished_at: float
    per_type: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def total_sampled(self) -> int:
        return sum(self.per_type.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": round(self.finished_at - self.started_at, 3),
            "total_sampled": self.total_sampled,
            "per_type": dict(self.per_type),
            "failures": dict(self.failures),
        }


class Sampler:
    """Draws a random slice of recent tasks per type and ships them to S3.

    Not a streaming pipeline — this is a periodic coarse snapshot. For
    high-throughput capture we'd want an outbox table + a streaming consumer,
    but the labelling workflow doesn't need that yet."""

    def __init__(
        self,
        *,
        s3: S3Writer,
        prefix: str = "labelling",
        window_hours: int = 24,
        per_type_limit: int = 500,
        types: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> None:
        self.s3 = s3
        self.prefix = prefix.strip("/")
        self.window_hours = max(1, window_hours)
        self.per_type_limit = max(1, per_type_limit)
        self.types = [t.strip() for t in (types or []) if t.strip()]
        self.statuses = [s.strip() for s in (statuses or ["ready", "failed"]) if s.strip()]

    @classmethod
    def from_env(cls, s3: S3Writer) -> Sampler:
        types_csv = os.getenv("PIPELINE_SAMPLE_TYPES", "")
        statuses_csv = os.getenv("PIPELINE_SAMPLE_STATUSES", "ready,failed")
        return cls(
            s3=s3,
            prefix=os.getenv("PIPELINE_S3_PREFIX", "labelling"),
            window_hours=int(os.getenv("PIPELINE_SAMPLE_WINDOW_HOURS", "24")),
            per_type_limit=int(os.getenv("PIPELINE_SAMPLE_PER_TYPE", "500")),
            types=types_csv.split(",") if types_csv else None,
            statuses=statuses_csv.split(",") if statuses_csv else None,
        )

    async def run_once(self) -> SampleReport:
        started = time.time()
        since = datetime.now(timezone.utc) - timedelta(hours=self.window_hours)

        async with async_session() as s:
            target_types = self.types or await _types_in_window(s, since=since, statuses=self.statuses)

        per_type: dict[str, int] = {}
        failures: dict[str, str] = {}
        for t in target_types:
            try:
                n = await self.sample_live_traffic(t, self.per_type_limit, since=since)
            except Exception as exc:  # noqa: BLE001 — don't let one bad type kill the run
                logger.exception("pipeline.sample.type_failed", type=t, error=str(exc))
                failures[t] = str(exc)
                continue
            per_type[t] = n
            logger.info("pipeline.sample.type_done", type=t, sampled=n)

        return SampleReport(started_at=started, finished_at=time.time(), per_type=per_type, failures=failures)

    async def sample_live_traffic(
        self,
        type_: str,
        n: int,
        *,
        since: datetime | None = None,
    ) -> int:
        """Random N tasks of `type_` since `since`, pushed to S3. Returns upload count."""
        since = since or datetime.now(timezone.utc) - timedelta(hours=self.window_hours)
        limit = max(1, min(n, self.per_type_limit))

        async with async_session() as s:
            # ORDER BY random() is fine at labelling-scale (bounded N, small type cardinality).
            # Switch to TABLESAMPLE SYSTEM(k) if the table grows past ~10M rows per type.
            stmt = (
                select(Task)
                .where(
                    Task.type == type_,
                    Task.created_at >= since,
                    Task.status.in_(self.statuses) if self.statuses else Task.status.isnot(None),
                )
                .order_by(func.random())
                .limit(limit)
            )
            rows = (await s.execute(stmt)).scalars().all()

        if not rows:
            return 0

        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        uploaded = 0
        for t in rows:
            key = f"{self.prefix}/{type_}/{day}/{t.id}.json"
            blob = _task_to_blob(t)
            if await self.s3.put_json(key, blob):
                uploaded += 1
        return uploaded


async def _types_in_window(
    session: AsyncSession,
    *,
    since: datetime,
    statuses: list[str],
) -> list[str]:
    stmt = (
        select(Task.type, func.count().label("n"))
        .where(Task.created_at >= since, Task.status.in_(statuses) if statuses else Task.status.isnot(None))
        .group_by(Task.type)
        .order_by(func.count().desc())
    )
    rows = (await session.execute(stmt)).all()
    return [str(r[0]) for r in rows if r[0]]


def _task_to_blob(t: Task) -> dict[str, Any]:
    latency_ms: float | None = None
    if t.finished_at and t.started_at:
        latency_ms = round((t.finished_at - t.started_at).total_seconds() * 1000.0, 1)
    return {
        "task_id": str(t.id),
        "type": t.type,
        "status": t.status,
        "payload": t.payload,
        "result": t.result,
        "error": t.error,
        "worker_id": t.worker_id,
        "retries": t.retries,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "latency_ms": latency_ms,
        "schema": 1,
    }
