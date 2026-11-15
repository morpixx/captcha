"""Pulls recent Task rows per type and dumps them to S3 for human labelling.

One Sampler per process. run_once() samples every configured type and returns a
SampleReport for logging / alerting.

Two extension points, both optional and both off by default:

* sample hooks - per-type callables that rewrite the uploaded blob. Visual types
  (hopscotch, grid heads, detectors) carry megabyte-sized base64 images;
  compact_image_hook() stubs those out so a labelling sample stays small.
* report hooks - callables that add a section to SampleReport.to_dict(), for
  whatever the ops dashboard wants next to per_type counts.

Nothing in here changes what gets sampled for types without a hook.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.adapters.storage import Task
from captcha.adapters.storage.engine import async_session
from captcha.domain.enums import CaptchaType
from captcha.services.pipeline.s3client import S3Writer

logger = structlog.get_logger(__name__)

#: hopscotch is a custom type; it has no CaptchaType member yet (hopscotch-ml owns
#: that change), so it is matched by its wire name.
HOPSCOTCH_TYPE = "hopscotch"

#: Types solved from rendered pixels - the ones whose payload holds an image.
#: Everything the ML artifacts in docs/ml.md cover, plus hopscotch.
VISUAL_TYPES: frozenset[str] = frozenset(
    {
        CaptchaType.IMAGE_TO_TEXT,
        CaptchaType.MATH_IMAGE,
        CaptchaType.ROTATE,
        CaptchaType.SLIDER,
        CaptchaType.CLICK,
        CaptchaType.COORDINATES,
        CaptchaType.BOUNDING_BOX,
        CaptchaType.CLASSIFICATION_GRID,
        CaptchaType.DRAW_AROUND,
        CaptchaType.DRAG_DROP,
        CaptchaType.HCAPTCHA,
        CaptchaType.HCAPTCHA_ENTERPRISE,
        CaptchaType.FUNCAPTCHA,
        CaptchaType.FUNCAPTCHA_BDA,
        CaptchaType.FUNCAPTCHA_ENTERPRISE,
        CaptchaType.AWSWAF,
        CaptchaType.SHEIN,
        CaptchaType.MTCAPTCHA,
        CaptchaType.TENCENT,
        CaptchaType.YIDUN,
        CaptchaType.HUNT,
        CaptchaType.BASILISK,
        CaptchaType.CAPTCHAFOX,
        CaptchaType.ATBCAPTCHA,
        CaptchaType.CAPY,
        CaptchaType.KEYCAPTCHA,
        CaptchaType.CUTCAPTCHA,
        HOPSCOTCH_TYPE,
    }
)

#: (task, blob) -> blob. Raising is safe: the raw blob is uploaded instead.
SampleHook = Callable[[Task, dict[str, Any]], dict[str, Any]]
#: report -> section merged into SampleReport.to_dict()["extra"].
ReportHook = Callable[["SampleReport"], dict[str, Any]]


@dataclass(slots=True)
class SampleReport:
    started_at: float
    finished_at: float
    per_type: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    #: task id -> why its sample hook failed (the raw blob was uploaded instead)
    hook_failures: dict[str, str] = field(default_factory=dict)
    #: sections produced by report hooks
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def total_sampled(self) -> int:
        return sum(self.per_type.values())

    @property
    def visual_per_type(self) -> dict[str, int]:
        return {t: n for t, n in self.per_type.items() if t in VISUAL_TYPES}

    def to_dict(self) -> dict[str, Any]:
        visual = self.visual_per_type
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_sec": round(self.finished_at - self.started_at, 3),
            "total_sampled": self.total_sampled,
            "per_type": dict(self.per_type),
            "failures": dict(self.failures),
            "visual": {"types": sorted(visual), "sampled": sum(visual.values())},
            "hook_failures": dict(self.hook_failures),
            "extra": dict(self.extra),
        }


class Sampler:
    """Draws a random slice of recent tasks per type and ships them to S3.

    Not a streaming pipeline - this is a periodic coarse snapshot. For
    high-throughput capture we would want an outbox table + a streaming consumer,
    but the labelling workflow does not need that yet.
    """

    def __init__(
        self,
        *,
        s3: S3Writer,
        prefix: str = "labelling",
        window_hours: int = 24,
        per_type_limit: int = 500,
        types: list[str] | None = None,
        statuses: list[str] | None = None,
        hooks: Mapping[str, SampleHook] | None = None,
        report_hooks: Iterable[ReportHook] = (),
    ) -> None:
        self.s3 = s3
        self.prefix = prefix.strip("/")
        self.window_hours = max(1, window_hours)
        self.per_type_limit = max(1, per_type_limit)
        self.types = [t.strip() for t in (types or []) if t.strip()]
        self.statuses = [s.strip() for s in (statuses or ["ready", "failed"]) if s.strip()]
        self.hooks = dict(hooks or {})
        self.report_hooks = tuple(report_hooks)
        self._hook_failures: dict[str, str] = {}

    @classmethod
    def from_env(cls, s3: S3Writer) -> Sampler:
        types_csv = os.getenv("PIPELINE_SAMPLE_TYPES", "")
        statuses_csv = os.getenv("PIPELINE_SAMPLE_STATUSES", "ready,failed")
        hooks: dict[str, SampleHook] = {}
        if _bool_env("PIPELINE_SAMPLE_COMPACT_IMAGES", default=True):
            hook = compact_image_hook(max_bytes=int(os.getenv("PIPELINE_SAMPLE_IMAGE_MAX_BYTES", "1048576")))
            hooks = dict.fromkeys(VISUAL_TYPES, hook)
        return cls(
            s3=s3,
            prefix=os.getenv("PIPELINE_S3_PREFIX", "labelling"),
            window_hours=int(os.getenv("PIPELINE_SAMPLE_WINDOW_HOURS", "24")),
            per_type_limit=int(os.getenv("PIPELINE_SAMPLE_PER_TYPE", "500")),
            types=types_csv.split(",") if types_csv else None,
            statuses=statuses_csv.split(",") if statuses_csv else None,
            hooks=hooks,
        )

    def visual_types(self, types: Iterable[str] | None = None) -> list[str]:
        """Which of the configured (or given) types are image-driven."""
        return sorted(set(self.types if types is None else types) & VISUAL_TYPES)

    async def run_once(self) -> SampleReport:
        started = time.time()
        since = datetime.now(UTC) - timedelta(hours=self.window_hours)

        async with async_session() as s:
            target_types = self.types or await _types_in_window(s, since=since, statuses=self.statuses)

        per_type: dict[str, int] = {}
        failures: dict[str, str] = {}
        for t in target_types:
            try:
                n = await self.sample_live_traffic(t, self.per_type_limit, since=since)
            except Exception as exc:
                logger.exception("pipeline.sample.type_failed", type=t, error=str(exc))
                failures[t] = str(exc)
                continue
            per_type[t] = n
            logger.info("pipeline.sample.type_done", type=t, sampled=n)

        report = SampleReport(
            started_at=started,
            finished_at=time.time(),
            per_type=per_type,
            failures=failures,
            hook_failures=dict(self._hook_failures),
        )
        self._hook_failures.clear()
        report.extra = self._report_extra(report)
        return report

    async def sample_live_traffic(
        self,
        type_: str,
        n: int,
        *,
        since: datetime | None = None,
    ) -> int:
        """Random N tasks of type_ since since, pushed to S3. Returns upload count."""
        since = since or datetime.now(UTC) - timedelta(hours=self.window_hours)
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

        day = datetime.now(UTC).strftime("%Y%m%d")
        uploaded = 0
        for t in rows:
            key = f"{self.prefix}/{type_}/{day}/{t.id}.json"
            blob = self._apply_hook(type_, t, _task_to_blob(t))
            if await self.s3.put_json(key, blob):
                uploaded += 1
        return uploaded

    # ---- hooks ---------------------------------------------------------

    def _apply_hook(self, type_: str, task: Task, blob: dict[str, Any]) -> dict[str, Any]:
        hook = self.hooks.get(type_)
        if hook is None:
            return blob
        try:
            return hook(task, blob)
        except Exception as exc:
            # A broken hook must not cost us the sample: upload what we had.
            logger.exception("pipeline.sample.hook_failed", type=type_, task_id=str(task.id), error=str(exc))
            self._hook_failures[str(task.id)] = f"{type_}: {exc}"
            return blob

    def _report_extra(self, report: SampleReport) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        for index, hook in enumerate(self.report_hooks):
            name = getattr(hook, "__name__", f"hook_{index}")
            if name == "<lambda>":
                name = f"hook_{index}"
            try:
                section = hook(report)
            except Exception as exc:
                logger.exception("pipeline.sample.report_hook_failed", hook=name, error=str(exc))
                extra.setdefault("report_hook_errors", {})[name] = str(exc)
                continue
            if isinstance(section, Mapping):
                extra.update(section)
        return extra


def compact_image_hook(*, max_bytes: int = 1024 * 1024, field_name: str = "image_b64") -> SampleHook:
    """Replace an oversized base64 image with a stub; keep the sample joinable.

    A labelling sample is small metadata plus a pointer, not a 32 MB payload:
    reviewers pull the original from the task envelope by id. The stub keeps a
    sha256 and the original size so duplicate/regression analysis still works.
    """
    limit = max(1024, int(max_bytes))

    def hook(task: Task, blob: dict[str, Any]) -> dict[str, Any]:
        payload = blob.get("payload")
        if not isinstance(payload, Mapping):
            return blob
        image = payload.get(field_name)
        if not isinstance(image, str) or len(image) <= limit:
            return blob
        stub = {
            "chars": len(image),
            "sha256": hashlib.sha256(image.encode("ascii", "replace")).hexdigest(),
        }
        new_payload = dict(payload)
        new_payload[field_name] = None
        new_payload[f"{field_name}_omitted"] = stub
        out = dict(blob)
        out["payload"] = new_payload
        return out

    return hook


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


def _bool_env(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
