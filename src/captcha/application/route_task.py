"""HTTP-first lane picker. Wraps domain.lanes.pick_lane with a bias toward the
HTTP lane for anything a text-protocol solver might handle, letting the
worker-side demotion rule do the final mode decision.
"""

from __future__ import annotations

from captcha.domain.enums import SolveMode
from captcha.domain.lanes import Lane, pick_lane
from captcha.domain.task import Task


def route_task(task: Task) -> Lane:
    """HTTP-first: if the dispatch map doesn't force a specialized lane and
    the client didn't insist on BROWSER, start on 'http' — the worker demotes
    to 'cv' / 'protocol' / 'browser' only when its success-rate window drops
    under the configured threshold.

    This is what the gateway writes on first enqueue. The worker's retry path
    is free to re-publish on a different lane.
    """
    if task.preferred_mode == SolveMode.BROWSER:
        return "browser"
    chosen = pick_lane(task)
    # HTTP-first bias: AUTO + cv/protocol candidate stays on http until the
    # worker gives up and reroutes. This keeps the fast path hot.
    if task.preferred_mode == SolveMode.AUTO and chosen in ("cv", "protocol"):
        return "http"
    return chosen
