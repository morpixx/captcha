"""Lane picker — the one place the gateway decides where a task goes.

The lane is a property of the captcha type (catalog), not of the request. One
worker process per lane, one stream per lane; a missing worker shows up as a
growing stream, not as a task that silently falls through to the wrong pool.
"""

from __future__ import annotations

from captcha.domain.lanes import Lane, pick_lane
from captcha.domain.task import Task


def route_task(task: Task) -> Lane:
    """Return the lane whose worker pool owns this captcha type."""
    return pick_lane(task)
