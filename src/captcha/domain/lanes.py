"""Lane routing. Reads the catalog; add new types there, not here."""

from __future__ import annotations

from captcha.domain.catalog import LANES, Lane, lane_for
from captcha.domain.enums import SolveMode
from captcha.domain.task import Task

__all__ = ["LANES", "Lane", "pick_lane"]


def pick_lane(task: Task) -> Lane:
    """Route a task to the lane that owns its solver workers.

    The lane normally comes from the catalog. `preferred_mode` is an internal
    routing hint set by the gateway/site profile, not a client knob: BROWSER
    forces the browser lane (hCaptcha/Riot), HTTP forces the HTTP lane for a
    type whose default lane is browser (hCaptcha/Steam-Epic HTTP probe).
    """
    if task.preferred_mode is SolveMode.BROWSER:
        return "browser"
    lane = lane_for(task.type)
    if task.preferred_mode is SolveMode.HTTP and lane == "browser":
        return "http"
    return lane
