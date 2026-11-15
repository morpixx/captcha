"""Shaping of per-account usage rows into the cabinet's stats payload.

Pure: takes plain rows out of the repository and returns JSON-ready dicts, so
the arithmetic (success rate, per-type rollup, daily buckets) is testable
without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class TypeRow:
    """One (type, status) aggregate straight from SQL."""

    type: str
    status: str
    count: int
    avg_latency_ms: float | None = None
    spend: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DayRow:
    day: Any  # date
    count: int
    spend: Decimal | None = None


@dataclass(slots=True)
class TypeStats:
    type: str
    tasks: int = 0
    ready: int = 0
    failed: int = 0
    spend: Decimal = Decimal("0")
    latency_samples: list[float] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        settled = self.ready + self.failed
        return round(self.ready / settled, 4) if settled else 0.0

    @property
    def avg_latency_ms(self) -> float | None:
        if not self.latency_samples:
            return None
        return round(sum(self.latency_samples) / len(self.latency_samples), 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "tasks": self.tasks,
            "ready": self.ready,
            "failed": self.failed,
            "success_rate": self.success_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "spend": str(self.spend),
        }


def summarize(
    type_rows: list[TypeRow],
    day_rows: list[DayRow],
    *,
    window_days: int,
) -> dict[str, Any]:
    """Roll rows up into {totals, by_type, daily}."""
    by_type: dict[str, TypeStats] = {}
    for row in type_rows:
        stats = by_type.setdefault(row.type, TypeStats(type=row.type))
        stats.tasks += int(row.count)
        if row.status == "ready":
            stats.ready += int(row.count)
        elif row.status == "failed":
            stats.failed += int(row.count)
        if row.spend:
            stats.spend += Decimal(row.spend)
        if row.avg_latency_ms is not None:
            stats.latency_samples.append(float(row.avg_latency_ms))

    ordered = sorted(by_type.values(), key=lambda s: s.tasks, reverse=True)
    total_tasks = sum(s.tasks for s in ordered)
    total_ready = sum(s.ready for s in ordered)
    total_failed = sum(s.failed for s in ordered)
    settled = total_ready + total_failed

    return {
        "window_days": window_days,
        "totals": {
            "tasks": total_tasks,
            "ready": total_ready,
            "failed": total_failed,
            "success_rate": round(total_ready / settled, 4) if settled else 0.0,
            "spend": str(sum((s.spend for s in ordered), Decimal("0"))),
        },
        "by_type": [s.to_dict() for s in ordered],
        "daily": [
            {
                "date": str(row.day),
                "tasks": int(row.count),
                "spend": str(row.spend or Decimal("0")),
            }
            for row in day_rows
        ],
    }
