"""Render a finished `LoadResult`: one flat text report, one dict for machines.

The layout is fixed so two runs are diffable: target line, phase table, outcome,
queue, errors, verdict. Units are milliseconds in the text form and `*_ms` in
the JSON; the raw config is echoed masked.
"""

from __future__ import annotations

from typing import Any

from .stats import LoadResult, MetricStats

_WIDTH = 78


def _ms(seconds: float) -> str:
    if seconds >= 1.0:
        return f"{seconds:.2f}s"
    return f"{seconds * 1000.0:.1f}"


def _phase_row(name: str, stats: MetricStats, per_sec: float = 0.0) -> str:
    rate = f"{per_sec:8.1f}" if per_sec else " " * 8
    return (
        f"{name:<16}{stats.count:>9}{rate}  "
        f"{_ms(stats.p50):>8}{_ms(stats.p95):>9}{_ms(stats.p99):>9}{_ms(stats.max):>9}"
    )


def _queue_line(queue: dict[str, Any]) -> str:
    if not queue.get("enabled"):
        return f"queue    not sampled ({queue.get('reason', 'no --redis-url')})"
    lag = queue["lag"]
    pending = queue["pending"]
    xlen = queue["xlen"]
    return (
        f"queue    lane={queue['lane']} lag avg={lag['avg']} max={lag['max']} | "
        f"pending avg={pending['avg']} max={pending['max']} | "
        f"xlen max={xlen['max']} | samples={lag['samples']}"
    )


def verdict_lines(result: LoadResult) -> list[str]:
    """Short, blunt reading of the run. Thresholds are the doc's thresholds."""
    out: list[str] = []
    ratio = result.target_ratio
    if ratio < 0.95:
        out.append(
            f"target missed: offered {result.target_rate:.0f}/s, achieved {result.achieved_rate:.0f}/s "
            f"({ratio * 100:.0f}%) — that is the ceiling of this environment or a saturated gateway"
        )
    if result.dropped:
        out.append(
            f"overload: {result.dropped} dispatches dropped at in-flight cap {result.config.get('in_flight_cap')} "
            "— the stack did not accept the offered rate"
        )
    if result.errors:
        top = next(iter(result.errors.items()))
        out.append(f"errors: {sum(result.errors.values())} total, top {top[0]} x{top[1]}")
    if result.unfinished:
        out.append(
            f"unfinished: {result.unfinished} tasks still in flight after drain ({result.unfinished} dropped from e2e)"
        )
    queue = result.queue
    if queue.get("enabled") and queue["lag"]["max"] > 1000:
        out.append(f"backlog: lane lag peaked at {queue['lag']['max']} entries — workers are behind")
    if not out:
        out.append("clean: target met, no errors, queue drained")
    return out


def format_text(result: LoadResult) -> str:
    lines: list[str] = []
    head = f"load run  {result.started_at}"
    if result.label:
        head += f"  [{result.label}]"
    lines.append(head)
    lines.append(
        f"target    {result.target_rate:.0f}/s for {result.duration:.0f}s  type={result.config.get('captcha_type')}  "
        f"cap={result.config.get('in_flight_cap')}  url={result.config.get('url')}"
    )
    lines.append("-" * _WIDTH)
    lines.append(f"{'phase':<16}{'n':>9}{'rate/s':>8}  {'p50':>8}{'p95':>9}{'p99':>9}{'max':>9}")
    lines.append(_phase_row("createTask", result.create, result.created / result.duration if result.duration else 0.0))
    lines.append(_phase_row("getTaskResult", result.poll, result.polls / result.duration if result.duration else 0.0))
    lines.append(_phase_row("create->ready", result.e2e))
    lines.append("-" * _WIDTH)
    lines.append(
        f"outcome   created={result.created} ready={result.ready} failed={result.failed} "
        f"solve_timeout={result.solve_timeout} unfinished={result.unfinished} dropped={result.dropped}"
    )
    if result.ready:
        lines.append(f"polls     {result.polls} total, {result.polls / result.ready:.2f} per solved task")
    lines.append(_queue_line(result.queue))
    if result.errors:
        for name, count in result.errors.items():
            lines.append(f"error     {name:<40}{count}")
    else:
        lines.append("error     none")
    lines.append("-" * _WIDTH)
    for line in verdict_lines(result):
        lines.append(f"verdict   {line}")
    return "\n".join(lines)


def format_buckets(result: LoadResult, limit: int = 40) -> str:
    """Optional per-bucket table: shows drift over a soak, not just the tail."""
    if not result.buckets:
        return ""
    rows = result.buckets
    step = max(1, len(rows) // limit)
    lines = [f"{'t':>7}{'created':>9}{'ready':>8}{'failed':>8}{'errors':>8}{'rps':>9}{'e2e p95':>10}"]
    for row in rows[::step]:
        lines.append(
            f"{row['t']:>7.0f}{row['created']:>9}{row['ready']:>8}{row['failed']:>8}{row['errors']:>8}"
            f"{row['rps']:>9.1f}{_ms(row['e2e_p95_ms'] / 1000.0):>10}"
        )
    return "\n".join(lines)
