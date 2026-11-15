"""Lane routing. Reads the catalog; add new types there, not here."""

from __future__ import annotations

from captcha.domain.catalog import LANES, Lane, lane_for
from captcha.domain.task import Task

__all__ = ["LANES", "Lane", "pick_lane"]


def pick_lane(task: Task) -> Lane:
    """Route a task to the lane that owns its solver workers.

    The lane is a property of the captcha type (catalog), not of the request:
    a client cannot ask for a different worker pool.
    """
    return lane_for(task.type)
