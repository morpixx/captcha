from __future__ import annotations

from uuid import UUID

from captcha.domain.enums import CaptchaType
from captcha.domain.lanes import LANES, Lane

__all__ = [
    "LANES",
    "Lane",
    "consumer_group",
    "dead_letter_stream",
    "stats_key",
    "stream_name",
    "task_hash_key",
]


def stream_name(prefix: str, lane: Lane) -> str:
    return f"{prefix}:stream:{lane}"


def task_hash_key(prefix: str, task_id: UUID | str) -> str:
    return f"{prefix}:task:{task_id}"


def stats_key(prefix: str, captcha_type: CaptchaType | str) -> str:
    t = captcha_type.value if isinstance(captcha_type, CaptchaType) else captcha_type
    return f"{prefix}:stats:{t}"


def dead_letter_stream(prefix: str) -> str:
    return f"{prefix}:dlq"


def consumer_group(lane: Lane) -> str:
    return f"group:lane-{lane}"
