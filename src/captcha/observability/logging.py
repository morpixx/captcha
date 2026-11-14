from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any, Literal

import structlog
from structlog.contextvars import merge_contextvars
from structlog.dev import ConsoleRenderer
from structlog.processors import (
    CallsiteParameter,
    CallsiteParameterAdder,
    JSONRenderer,
    TimeStamper,
    UnicodeDecoder,
    format_exc_info,
)
from structlog.stdlib import add_log_level

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def _inject_correlation_id(_logger: Any, _name: str, event: dict[str, Any]) -> dict[str, Any]:
    cid = correlation_id.get()
    if cid is not None:
        event.setdefault("correlation_id", cid)
    return event


def setup_logging(level: LogLevel = "INFO", json: bool = True) -> None:
    """Configure structlog + stdlib logging. Idempotent — safe to call from every entrypoint."""
    numeric_level = logging.getLevelNamesMapping().get(level, logging.INFO)

    # route stdlib logging to stdout with no formatting — structlog owns the line
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # quiet the usual suspects — leave captcha_* alone
    for noisy in ("urllib3", "asyncio", "botocore", "httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(max(numeric_level, logging.WARNING))

    processors: list[structlog.types.Processor] = [
        merge_contextvars,
        _inject_correlation_id,
        add_log_level,
        TimeStamper(fmt="iso", utc=True, key="ts"),
        CallsiteParameterAdder(
            parameters=(
                CallsiteParameter.MODULE,
                CallsiteParameter.FUNC_NAME,
                CallsiteParameter.LINENO,
            )
        ),
        structlog.processors.StackInfoRenderer(),
        format_exc_info,
        UnicodeDecoder(),
    ]

    if json:
        processors.append(JSONRenderer(sort_keys=False))
    else:
        processors.append(ConsoleRenderer(colors=sys.stdout.isatty()))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
