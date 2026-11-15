"""Driver configuration: one frozen dataclass, built from argv.

Everything that changes a number in the report lives here, and the report
echoes the whole object back, so a run can be replayed from the JSON it wrote.
The client key is masked on the way out.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
from dataclasses import dataclass
from typing import Any

DEFAULT_TYPE = "text_captcha"
DEFAULT_SITE_URL = "https://example.com"
DEFAULT_SITE_KEY = "load-test-key"


@dataclass(frozen=True, slots=True)
class LoadConfig:
    """Target and pacing. All durations are seconds; the report renders ms."""

    url: str = "http://127.0.0.1:8080"
    client_key: str = ""
    captcha_type: str = DEFAULT_TYPE
    site_url: str = DEFAULT_SITE_URL
    site_key: str = DEFAULT_SITE_KEY
    #: Offered task creations per second (open loop — see `scenario.run_load`).
    rate: float = 100.0
    duration: float = 60.0
    #: Per-request HTTP timeout. A request slower than this counts as an error.
    timeout: float = 15.0
    #: First poll delay and the backoff ceiling for a task that is not ready yet.
    poll_interval: float = 0.05
    poll_max_interval: float = 1.0
    #: Give up on a task after this long since its createTask started.
    solve_timeout: float = 180.0
    #: Concurrent virtual tasks; 0 derives a cap from the rate.
    in_flight_max: int = 0
    #: Set to sample queue depth (XLEN + consumer-group lag) straight from Redis.
    redis_url: str = ""
    redis_prefix: str = "captcha"
    #: Stream lane to sample; empty derives it from `captcha.domain.catalog`.
    lane: str = ""
    bucket_sec: float = 15.0
    report_json: str = ""
    progress_sec: float = 30.0
    label: str = ""

    @property
    def in_flight_cap(self) -> int:
        """Hard cap on concurrent virtual tasks; 0 means derived from the rate."""
        if self.in_flight_max > 0:
            return self.in_flight_max
        return max(64, int(self.rate))

    def payload(self) -> dict[str, Any]:
        """The createTask body. Minimal on purpose: type + site fields validate
        against the gateway schema without dragging in a real image or key."""
        return {
            "clientKey": self.client_key,
            "task": {
                "type": self.captcha_type,
                "websiteKey": self.site_key,
                "websiteURL": self.site_url,
            },
        }

    def as_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["client_key"] = "***" if self.client_key else ""
        data["in_flight_cap"] = self.in_flight_cap
        return data


def build_parser() -> argparse.ArgumentParser:
    # `slots=True` turns class attributes into member descriptors, so the
    # defaults have to come from an instance, not from `LoadConfig.rate`.
    defaults = LoadConfig()
    p = argparse.ArgumentParser(
        prog="python -m tools.load.run",
        description="Open-loop createTask/getTaskResult load test.",
    )
    p.add_argument("--url", default=defaults.url, help="gateway base URL")
    p.add_argument(
        "--key",
        dest="client_key",
        default=os.environ.get("LOAD_CLIENT_KEY", ""),
        help="clientKey of a funded account (default: $LOAD_CLIENT_KEY)",
    )
    p.add_argument("--type", dest="captcha_type", default=defaults.captcha_type, help="2captcha type to create")
    p.add_argument("--site-url", dest="site_url", default=defaults.site_url, help="websiteURL sent in the task")
    p.add_argument("--site-key", dest="site_key", default=defaults.site_key, help="websiteKey sent in the task")
    p.add_argument("--rate", type=float, default=defaults.rate, help="offered task creations per second")
    p.add_argument("--duration", type=float, default=defaults.duration, help="seconds of offered load")
    p.add_argument("--timeout", type=float, default=defaults.timeout, help="per-request HTTP timeout")
    p.add_argument("--poll-interval", type=float, default=defaults.poll_interval, help="first poll delay")
    p.add_argument("--poll-max-interval", type=float, default=defaults.poll_max_interval, help="backoff ceiling")
    p.add_argument("--solve-timeout", type=float, default=defaults.solve_timeout, help="per-task deadline")
    p.add_argument(
        "--in-flight-max",
        type=int,
        default=defaults.in_flight_max,
        help="concurrent virtual tasks (0: derived from --rate)",
    )
    p.add_argument("--redis-url", default=defaults.redis_url, help="sample queue depth from this Redis")
    p.add_argument("--redis-prefix", default=defaults.redis_prefix, help="stream key prefix")
    p.add_argument("--lane", default=defaults.lane, help="lane to sample (default: from catalog)")
    p.add_argument("--bucket-sec", type=float, default=defaults.bucket_sec, help="time-series bucket width")
    p.add_argument("--json", dest="report_json", default=defaults.report_json, help="write the report as JSON here")
    p.add_argument("--progress-sec", type=float, default=defaults.progress_sec, help="progress line interval")
    p.add_argument("--label", default=defaults.label, help="free-form label stored in the report")
    p.add_argument("--strict", action="store_true", help="exit 2 when errors occurred or the target rate was missed")
    return p


def config_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> LoadConfig:
    if not args.client_key:
        parser.error("--key (or $LOAD_CLIENT_KEY) is required")
    if args.rate <= 0:
        parser.error("--rate must be positive")
    if args.duration <= 0:
        parser.error("--duration must be positive")
    fields = {f.name for f in dataclasses.fields(LoadConfig)}
    kwargs = {k: v for k, v in vars(args).items() if k in fields}
    return LoadConfig(**kwargs)
