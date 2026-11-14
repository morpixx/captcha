from __future__ import annotations

from typing import Literal

from captcha.domain.enums import CaptchaType, SolveMode
from captcha.domain.task import Task

Lane = Literal["http", "cv", "protocol", "browser"]

LANES: tuple[Lane, ...] = ("http", "cv", "protocol", "browser")

# CV lane: anything that reduces to an image/audio/geometry solve.
_CV_TYPES: frozenset[CaptchaType] = frozenset(
    {
        CaptchaType.IMAGE_TO_TEXT,
        CaptchaType.MATH_IMAGE,
        CaptchaType.TEXT,
        CaptchaType.AUDIO,
        CaptchaType.CLASSIFICATION_GRID,
        CaptchaType.CLICK,
        CaptchaType.COORDINATES,
        CaptchaType.BOUNDING_BOX,
        CaptchaType.DRAW_AROUND,
        CaptchaType.DRAG_DROP,
        CaptchaType.ROTATE,
        CaptchaType.SLIDER,
    }
)

# Protocol lane: pure proof-of-work / client-side verifier solvers.
_PROTOCOL_TYPES: frozenset[CaptchaType] = frozenset(
    {
        CaptchaType.FRIENDLY,
        CaptchaType.ALTCHA,
    }
)


def pick_lane(task: Task) -> Lane:
    """Route a task to the lane that owns its solver workers.

    Order matters: an explicit BROWSER preference overrides the type default,
    since some clients insist on a real browser (session-sticky antibot chains,
    for example). AUTO falls through to type-based routing.
    """
    if task.preferred_mode == SolveMode.BROWSER:
        return "browser"
    if task.type in _CV_TYPES:
        return "cv"
    if task.type in _PROTOCOL_TYPES:
        return "protocol"
    return "http"
