"""The report is a stable contract: rows exist, units are ms, verdicts fire."""

from __future__ import annotations

from tools.load.report import format_buckets, format_text, verdict_lines
from tools.load.stats import LoadResult, MetricStats


def _stats(p50: float = 0.002, p95: float = 0.01, p99: float = 0.02, max_: float = 0.05, count: int = 100):
    return MetricStats(count=count, avg=p50, p50=p50, p95=p95, p99=p99, max=max_)


def _result(**overrides) -> LoadResult:
    base = {
        "started_at": "2026-11-15T00:00:00+00:00",
        "config": {"rate": 1000.0, "captcha_type": "text_captcha", "url": "http://x", "in_flight_cap": 1000},
        "duration": 60.0,
        "elapsed": 61.0,
        "created": 60_000,
        "ready": 59_990,
        "failed": 5,
        "solve_timeout": 5,
        "unfinished": 0,
        "dropped": 0,
        "polls": 90_000,
        "create": _stats(),
        "poll": _stats(),
        "e2e": _stats(p50=0.06, p95=0.2, p99=0.4, max_=0.9),
        "errors": {},
        "buckets": [
            {"t": 0.0, "created": 1000, "ready": 1000, "failed": 0, "errors": 0, "rps": 1000.0, "e2e_p95_ms": 200.0}
        ],
        "queue": {
            "enabled": True,
            "lane": "cv",
            "stream": "captcha:stream:cv",
            "group": "group:lane-cv",
            "lag": {"samples": 60, "avg": 0.5, "max": 12},
            "pending": {"samples": 60, "avg": 3.0, "max": 40},
            "xlen": {"samples": 60, "avg": 500.0, "max": 900},
            "sample_errors": 0,
        },
        "label": "soak",
    }
    base.update(overrides)
    return LoadResult(**base)


def test_text_report_has_every_section() -> None:
    text = format_text(_result())
    assert "load run" in text
    assert "[soak]" in text
    assert "createTask" in text
    assert "getTaskResult" in text
    assert "create->ready" in text
    assert "outcome" in text
    assert "polls     90000 total, 1.50 per solved task" in text
    assert "queue    lane=cv" in text
    assert "error     none" in text
    assert "verdict" in text
    assert "2.0" in text  # p50 of createTask rendered as ms


def test_clean_run_verdict_says_clean() -> None:
    lines = verdict_lines(_result())
    assert lines and lines[0].startswith("clean")


def test_missed_target_and_overload_are_called_out() -> None:
    result = _result(created=500, duration=60.0, dropped=42, errors={"createTask:http_503": 3})
    lines = verdict_lines(result)
    joined = " ".join(lines)
    assert "target missed" in joined
    assert "overload" in joined
    assert "503" in joined


def test_backlog_verdict_triggers_on_lag() -> None:
    result = _result(
        queue={
            "enabled": True,
            "lane": "http",
            "lag": {"samples": 1, "avg": 2000.0, "max": 5000},
            "pending": {"samples": 1, "avg": 0.0, "max": 0},
            "xlen": {"samples": 1, "avg": 0.0, "max": 0},
            "sample_errors": 0,
        }
    )
    assert any("backlog" in line for line in verdict_lines(result))


def test_queue_section_is_optional() -> None:
    text = format_text(_result(queue={"enabled": False, "reason": "no redis"}))
    assert "not sampled (no redis)" in text


def test_bucket_table_renders_rows() -> None:
    table = format_buckets(_result())
    assert "rps" in table
    assert "e2e p95" in table


def test_json_dict_is_machine_readable_and_masked() -> None:
    payload = _result(config={"rate": 1000.0, "client_key": "***"}).as_dict()
    assert payload["throughput"]["achieved_rate"] == 1000.0
    assert payload["latency"]["createTask"]["p50_ms"] == 2.0
    assert payload["config"]["client_key"] == "***"
    assert payload["queue"]["lag"]["max"] == 12
