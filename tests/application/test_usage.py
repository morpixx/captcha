"""Pure rollup of usage rows — the numbers a client sees in the cabinet."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from captcha.application.usage import DayRow, TypeRow, summarize


def test_totals_and_success_rate() -> None:
    rows = [
        TypeRow(type="hcaptcha", status="ready", count=8, avg_latency_ms=400.0, spend=Decimal("0.012")),
        TypeRow(type="hcaptcha", status="failed", count=2, avg_latency_ms=100.0, spend=Decimal("0")),
    ]
    out = summarize(rows, [], window_days=7)

    assert out["totals"]["tasks"] == 10
    assert out["totals"]["ready"] == 8
    assert out["totals"]["failed"] == 2
    assert out["totals"]["success_rate"] == 0.8
    assert out["totals"]["spend"] == "0.012"


def test_by_type_is_sorted_by_volume() -> None:
    rows = [
        TypeRow(type="altcha", status="ready", count=1),
        TypeRow(type="hcaptcha", status="ready", count=9),
    ]
    out = summarize(rows, [], window_days=1)
    assert [t["type"] for t in out["by_type"]] == ["hcaptcha", "altcha"]


def test_latency_is_averaged_per_type() -> None:
    rows = [
        TypeRow(type="click", status="ready", count=1, avg_latency_ms=100.0),
        TypeRow(type="click", status="failed", count=1, avg_latency_ms=300.0),
    ]
    out = summarize(rows, [], window_days=1)
    assert out["by_type"][0]["avg_latency_ms"] == 200.0


def test_daily_buckets_and_empty_window() -> None:
    out = summarize([], [DayRow(day=date(2026, 11, 14), count=3, spend=Decimal("0.003"))], window_days=30)
    assert out["totals"]["tasks"] == 0
    assert out["totals"]["success_rate"] == 0.0
    assert out["daily"] == [{"date": "2026-11-14", "tasks": 3, "spend": "0.003"}]
