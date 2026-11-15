"""Lane routing. Reads the catalog; add new types there, not here."""

from __future__ import annotations

from captcha.domain.catalog import LANES, Lane, browser_capable, lane_for
from captcha.domain.enums import SolveMode
from captcha.domain.task import Task

__all__ = ["LANES", "Lane", "pick_lane"]


def pick_lane(task: Task) -> Lane:
    """Route a task to the lane that owns its solver workers.

    The lane normally comes from the catalog. `preferred_mode` is an internal
    routing hint set by the gateway/site profile, not a client knob: BROWSER
    forces the browser lane (hCaptcha/Riot), HTTP forces the HTTP lane for a
    type whose default lane is browser (hCaptcha/Steam-Epic HTTP probe).

    A BROWSER hint is honoured only when the catalog proves a `solve_browser`
    exists (`TypeSpec.browser`); an HTTP hint only probes browser-lane types,
    and tests/services pins that each of those has an HTTP solver. Without the
    guard, a BROWSER hint on a cv/http type with no browser solver parked the
    task in a lane with no solver — terminal `ERR_UNSOLVABLE` after a refund.
    """
    lane = lane_for(task.type)
    if task.preferred_mode is SolveMode.BROWSER:
        return "browser" if browser_capable(task.type) else lane
    if task.preferred_mode is SolveMode.HTTP and lane == "browser":
        return "http"
    return lane
