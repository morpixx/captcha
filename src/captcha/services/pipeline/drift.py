"""Per-type success-rate drift monitor.

For each captcha type seen in the recent window, compare success rate to the
prior-week baseline. Flag absolute (recent < threshold) and relative
(recent/baseline < threshold) drops. Fires an optional webhook."""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.adapters.storage import Task
from captcha.adapters.storage.engine import async_session

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class DriftFlag:
    type_: str
    recent_n: int
    recent_rate: float
    baseline_n: int
    baseline_rate: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type_,
            "recent_n": self.recent_n,
            "recent_rate": round(self.recent_rate, 4),
            "baseline_n": self.baseline_n,
            "baseline_rate": round(self.baseline_rate, 4),
            "delta": round(self.recent_rate - self.baseline_rate, 4),
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class DriftReport:
    started_at: float
    finished_at: float
    recent_window_hours: int
    baseline_days: int
    flags: list[DriftFlag] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "recent_window_hours": self.recent_window_hours,
            "baseline_days": self.baseline_days,
            "flags": [f.to_dict() for f in self.flags],
        }


class DriftMonitor:
    def __init__(
        self,
        *,
        window_hours: int = 6,
        baseline_days: int = 7,
        min_samples: int = 50,
        abs_threshold: float = 0.75,
        rel_threshold: float = 0.85,
        webhook_url: str = "",
        hostname: str | None = None,
    ) -> None:
        self.window_hours = max(1, window_hours)
        self.baseline_days = max(1, baseline_days)
        self.min_samples = max(1, min_samples)
        self.abs_threshold = _clamp(abs_threshold)
        self.rel_threshold = _clamp(rel_threshold)
        self.webhook_url = webhook_url
        self.hostname = hostname or socket.gethostname()

    @classmethod
    def from_env(cls) -> DriftMonitor:
        return cls(
            window_hours=int(os.getenv("PIPELINE_DRIFT_WINDOW_HOURS", "6")),
            baseline_days=int(os.getenv("PIPELINE_DRIFT_BASELINE_DAYS", "7")),
            min_samples=int(os.getenv("PIPELINE_DRIFT_MIN_SAMPLES", "50")),
            abs_threshold=float(os.getenv("PIPELINE_DRIFT_ABS_THRESHOLD", "0.75")),
            rel_threshold=float(os.getenv("PIPELINE_DRIFT_REL_THRESHOLD", "0.85")),
            webhook_url=os.getenv("PIPELINE_DRIFT_WEBHOOK", ""),
        )

    async def run_once(self) -> DriftReport:
        started = time.time()
        now = datetime.now(timezone.utc)
        recent_from = now - timedelta(hours=self.window_hours)
        baseline_to = recent_from  # baseline ends where recent starts
        baseline_from = recent_from - timedelta(days=self.baseline_days)

        async with async_session() as s:
            recent = await _rates(s, start=recent_from, end=now)
            baseline = await _rates(s, start=baseline_from, end=baseline_to)

        flags: list[DriftFlag] = []
        baseline_map = {r["type"]: r for r in baseline}
        for r in recent:
            if r["denom"] < self.min_samples:
                continue
            b = baseline_map.get(r["type"])
            b_rate = b["rate"] if b and b["denom"] > 0 else 0.0
            b_n = int(b["denom"]) if b else 0
            reasons: list[str] = []
            if r["rate"] < self.abs_threshold:
                reasons.append(f"abs<{self.abs_threshold}")
            # only check relative if baseline has enough signal; otherwise absolute is enough
            if b and b["denom"] >= self.min_samples and b_rate > 0:
                ratio = r["rate"] / b_rate
                if ratio < self.rel_threshold:
                    reasons.append(f"rel<{self.rel_threshold} (ratio={ratio:.2f})")
            if reasons:
                flags.append(
                    DriftFlag(
                        type_=r["type"],
                        recent_n=int(r["denom"]),
                        recent_rate=float(r["rate"]),
                        baseline_n=b_n,
                        baseline_rate=float(b_rate),
                        reasons=reasons,
                    )
                )

        report = DriftReport(
            started_at=started,
            finished_at=time.time(),
            recent_window_hours=self.window_hours,
            baseline_days=self.baseline_days,
            flags=flags,
        )
        if flags:
            for f in flags:
                logger.warning(
                    "pipeline.drift.flagged",
                    type=f.type_,
                    recent_rate=round(f.recent_rate, 4),
                    baseline_rate=round(f.baseline_rate, 4),
                    recent_n=f.recent_n,
                    reasons=f.reasons,
                )
            await self._alert(report)
        else:
            logger.info("pipeline.drift.clean", types_checked=len(recent))
        return report

    async def _alert(self, report: DriftReport) -> bool:
        if not self.webhook_url:
            return False
        payload = {
            "source": "captcha-pipeline",
            "kind": "drift",
            "hostname": self.hostname,
            "report": report.to_dict(),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(self.webhook_url, json=payload)
        except httpx.HTTPError as exc:
            logger.error("pipeline.drift.alert_transport", error=str(exc))
            return False
        if r.status_code >= 400:
            logger.error("pipeline.drift.alert_rejected", status=r.status_code, body=r.text[:300])
            return False
        logger.info("pipeline.drift.alert_sent", status=r.status_code, flags=len(report.flags))
        return True


# ---------- SQL helpers ----------


async def _rates(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    ok = func.sum(case((Task.status == "ready", 1), else_=0))
    bad = func.sum(case((Task.status == "failed", 1), else_=0))
    stmt = (
        select(
            Task.type.label("type"),
            ok.label("ok"),
            bad.label("bad"),
        )
        .where(Task.created_at >= start, Task.created_at < end)
        .where(Task.status.in_(("ready", "failed")))
        .group_by(Task.type)
    )
    rows = (await session.execute(stmt)).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        ok_n = int(r["ok"] or 0)
        bad_n = int(r["bad"] or 0)
        denom = ok_n + bad_n
        out.append(
            {
                "type": r["type"],
                "ok": ok_n,
                "bad": bad_n,
                "denom": denom,
                "rate": (ok_n / denom) if denom else 0.0,
            }
        )
    return out


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))
