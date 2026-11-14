from __future__ import annotations

from typing import Literal
from uuid import UUID

from captcha_common.enums import CaptchaType

Lane = Literal["http", "cv", "protocol", "browser"]

LANES: tuple[Lane, ...] = ("http", "cv", "protocol", "browser")


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
