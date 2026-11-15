"""Latency digests, error tallies and time buckets for the load driver.

No I/O and no clock reads: callers hand in elapsed seconds, which keeps runs
reproducible and makes the percentiles testable against hand-written samples.
Percentiles use linear interpolation between closest ranks — the same definition
numpy calls the default, so an external tool agrees with this one.
"""

from __future__ import annotations

import math
from array import array
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

DEFAULT_BUCKET_SEC = 15.0


def quantile(sorted_values: list[float], q: float) -> float:
    """Percentile of an already-sorted sample list, q in [0, 1]."""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_values[0]
    pos = (n - 1) * min(max(q, 0.0), 1.0)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


@dataclass(frozen=True, slots=True)
class MetricStats:
    """One phase's latency distribution, in seconds."""

    count: int = 0
    avg: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    max: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "avg_ms": round(self.avg * 1000.0, 2),
            "p50_ms": round(self.p50 * 1000.0, 2),
            "p95_ms": round(self.p95 * 1000.0, 2),
            "p99_ms": round(self.p99 * 1000.0, 2),
            "max_ms": round(self.max * 1000.0, 2),
        }


class LatencyDigest:
    """Keeps every sample so percentiles are exact, not approximated.

    `array('d')` holds 8 bytes per sample: a 15-minute soak at 1k/s is ~5 MB per
    phase. The sorted copy is built once, on the first percentile query.
    """

    __slots__ = ("_max", "_samples", "_sorted", "_total")

    def __init__(self) -> None:
        self._samples = array("d")
        self._sorted: list[float] | None = None
        self._total = 0.0
        self._max = 0.0

    @property
    def count(self) -> int:
        return len(self._samples)

    def observe(self, seconds: float) -> None:
        value = seconds if seconds > 0.0 else 0.0
        self._samples.append(value)
        self._total += value
        if value > self._max:
            self._max = value
        self._sorted = None

    def _values(self) -> list[float]:
        if self._sorted is None:
            self._sorted = sorted(self._samples)
        return self._sorted

    def percentile(self, q: float) -> float:
        return quantile(self._values(), q)

    def stats(self) -> MetricStats:
        n = len(self._samples)
        if n == 0:
            return MetricStats()
        values = self._values()
        return MetricStats(
            count=n,
            avg=self._total / n,
            p50=quantile(values, 0.50),
            p95=quantile(values, 0.95),
            p99=quantile(values, 0.99),
            max=self._max,
        )


class ErrorTally:
    """Counts failures by `phase:kind`; the kind is an error code, an HTTP
    status or an exception class name — never a response body."""

    __slots__ = ("_counts",)

    def __init__(self) -> None:
        self._counts: Counter[str] = Counter()

    def add(self, phase: str, kind: str, n: int = 1) -> None:
        self._counts[f"{phase}:{kind}"] += n

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    def top(self, n: int = 10) -> list[tuple[str, int]]:
        return self._counts.most_common(n)

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self._counts.items(), key=lambda kv: -kv[1]))


@dataclass(slots=True)
class _Bucket:
    created: int = 0
    ready: int = 0
    failed: int = 0
    errors: int = 0
    e2e: array = field(default_factory=lambda: array("d"))


class TimeBuckets:
    """Fixed-width buckets so a soak shows drift and not just one p99.

    Stores every end-to-end sample per bucket: 15 s at 1k/s is 15k doubles, and
    the whole 15-minute run stays under 10 MB.
    """

    __slots__ = ("_buckets", "_width")

    def __init__(self, bucket_sec: float = DEFAULT_BUCKET_SEC) -> None:
        self._width = bucket_sec if bucket_sec > 0 else DEFAULT_BUCKET_SEC
        self._buckets: dict[int, _Bucket] = {}

    @property
    def width(self) -> float:
        return self._width

    def at(self, offset: float) -> _Bucket:
        return self._buckets.setdefault(int(offset // self._width), _Bucket())

    def rows(self) -> list[dict[str, Any]]:
        if not self._buckets:
            return []
        lo, hi = min(self._buckets), max(self._buckets)
        out: list[dict[str, Any]] = []
        for idx in range(lo, hi + 1):
            start = round(idx * self._width, 1)
            bucket = self._buckets.get(idx)
            if bucket is None:
                out.append(
                    {
                        "t": start,
                        "created": 0,
                        "ready": 0,
                        "failed": 0,
                        "errors": 0,
                        "rps": 0.0,
                        "e2e_p95_ms": 0.0,
                    }
                )
                continue
            samples = sorted(bucket.e2e)
            out.append(
                {
                    "t": start,
                    "created": bucket.created,
                    "ready": bucket.ready,
                    "failed": bucket.failed,
                    "errors": bucket.errors,
                    "rps": round(bucket.created / self._width, 2),
                    "e2e_p95_ms": round(quantile(samples, 0.95) * 1000.0, 1) if samples else 0.0,
                }
            )
        return out


@dataclass(slots=True)
class LoadResult:
    """Everything the report needs. Built once, at the end of a run."""

    started_at: str
    config: dict[str, Any]
    duration: float
    elapsed: float
    created: int
    ready: int
    failed: int
    solve_timeout: int
    unfinished: int
    dropped: int
    polls: int
    create: MetricStats
    poll: MetricStats
    e2e: MetricStats
    errors: dict[str, int]
    buckets: list[dict[str, Any]]
    queue: dict[str, Any]
    label: str = ""

    @property
    def achieved_rate(self) -> float:
        return self.created / self.duration if self.duration > 0 else 0.0

    @property
    def target_rate(self) -> float:
        return float(self.config.get("rate", 0.0) or 0.0)

    @property
    def target_ratio(self) -> float:
        target = self.target_rate
        return self.achieved_rate / target if target > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "started_at": self.started_at,
            "duration_sec": round(self.duration, 3),
            "elapsed_sec": round(self.elapsed, 3),
            "config": self.config,
            "throughput": {
                "target_rate": self.target_rate,
                "achieved_rate": round(self.achieved_rate, 2),
                "target_ratio": round(self.target_ratio, 4),
                "created": self.created,
                "ready": self.ready,
                "failed": self.failed,
                "solve_timeout": self.solve_timeout,
                "unfinished": self.unfinished,
                "dropped_overload": self.dropped,
                "polls": self.polls,
            },
            "latency": {
                "createTask": self.create.as_dict(),
                "getTaskResult": self.poll.as_dict(),
                "create_to_ready": self.e2e.as_dict(),
            },
            "errors": self.errors,
            "buckets": self.buckets,
            "queue": self.queue,
        }
