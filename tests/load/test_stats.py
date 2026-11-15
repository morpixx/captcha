"""Percentiles, buckets and the error tally — pure functions, hand-checked."""

from __future__ import annotations

from tools.load.stats import ErrorTally, LatencyDigest, TimeBuckets, quantile


def test_quantile_interpolates_between_ranks() -> None:
    values = [1.0, 2.0, 3.0, 4.0]
    assert quantile(values, 0.0) == 1.0
    assert quantile(values, 1.0) == 4.0
    assert quantile(values, 0.5) == 2.5
    # position 2.85 -> 3 + (4-3)*0.85
    assert round(quantile(values, 0.95), 4) == 3.85


def test_quantile_of_empty_and_single_sample() -> None:
    assert quantile([], 0.95) == 0.0
    assert quantile([0.25], 0.99) == 0.25


def test_digest_stats_cover_count_avg_and_tail() -> None:
    digest = LatencyDigest()
    for value in (0.001, 0.002, 0.003, 0.004):
        digest.observe(value)
    stats = digest.stats()
    assert stats.count == 4
    assert round(stats.avg, 6) == 0.0025
    assert stats.p50 == 0.0025
    assert stats.max == 0.004


def test_digest_ignores_negative_samples() -> None:
    digest = LatencyDigest()
    digest.observe(-1.0)
    assert digest.stats().max == 0.0
    assert digest.stats().count == 1


def test_digest_recomputes_after_new_sample() -> None:
    digest = LatencyDigest()
    digest.observe(0.010)
    assert digest.percentile(0.5) == 0.010
    digest.observe(0.002)
    assert digest.percentile(0.5) == 0.006


def test_time_buckets_fill_gaps_and_count() -> None:
    buckets = TimeBuckets(bucket_sec=10.0)
    first = buckets.at(1.0)
    first.created += 3
    first.ready += 2
    first.e2e.append(0.05)
    buckets.at(25.0).created += 1
    rows = buckets.rows()
    assert [row["t"] for row in rows] == [0.0, 10.0, 20.0]
    assert rows[0]["created"] == 3
    assert rows[0]["rps"] == 0.3
    assert rows[0]["e2e_p95_ms"] == 50.0
    assert rows[1]["created"] == 0
    assert rows[2]["created"] == 1


def test_error_tally_groups_by_phase_and_sorts_by_count() -> None:
    tally = ErrorTally()
    tally.add("createTask", "http_500")
    tally.add("createTask", "http_500")
    tally.add("getTaskResult", "ERR_PROXY")
    assert tally.total == 3
    assert tally.top(1) == [("createTask:http_500", 2)]
    assert list(tally.as_dict()) == ["createTask:http_500", "getTaskResult:ERR_PROXY"]
