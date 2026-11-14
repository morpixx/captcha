"""Metrics wiring. The registry and all counters live in captcha.observability;
the gateway just mounts the Starlette app and exposes a handful of locals it
re-increments at the API edge.
"""
from __future__ import annotations

from captcha.observability.metrics import (
    REGISTRY,
    build_metrics_app,
    tasks_created_total,
    tasks_done_total,
)

__all__ = ["REGISTRY", "build_metrics_app", "tasks_created_total", "tasks_done_total"]
