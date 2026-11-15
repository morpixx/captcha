"""Lane/stream naming and a probe with no Redis configured."""

from __future__ import annotations

from tools.load.queue_probe import QueueProbe, group_for, lane_for_type, stream_for


def test_lane_comes_from_the_catalog() -> None:
    assert lane_for_type("text_captcha") == "cv"
    assert lane_for_type("friendly_captcha") == "protocol"
    assert lane_for_type("not_a_type") == "http"


def test_stream_and_group_names_match_the_adapters() -> None:
    assert stream_for("captcha", "cv") == "captcha:stream:cv"
    assert group_for("cv") == "group:lane-cv"


async def test_probe_without_redis_reports_itself_disabled() -> None:
    probe = QueueProbe()
    await probe.start()
    try:
        summary = probe.summary()
    finally:
        await probe.stop()
    assert summary == {"enabled": False, "reason": "not configured"}
