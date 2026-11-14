"""Pure domain layer. stdlib only — no network, no database, no logging framework.

Everything here is a decision we own: the captcha taxonomy, task lifecycle,
error codes, prices, and the shape of a solution. Nothing in this package can
break because a vendor shipped a new build.
"""

from .enums import AntibotEngine, CaptchaType, Priority, SolveMode, TaskStatus
from .errors import (
    CaptchaError,
    ChallengeExpiredError,
    ConfigError,
    JSRuntimeError,
    ModelInferenceError,
    ProxyError,
    SensorError,
    TimeoutError,
    UnsolvableError,
)
from .solution import build_solution, host_for_payload
from .task import SolverResult, Task, TaskEnvelope, new_task_id

__all__ = [
    "AntibotEngine",
    "CaptchaError",
    "CaptchaType",
    "ChallengeExpiredError",
    "ConfigError",
    "JSRuntimeError",
    "ModelInferenceError",
    "Priority",
    "ProxyError",
    "SensorError",
    "SolveMode",
    "SolverResult",
    "Task",
    "TaskEnvelope",
    "TaskStatus",
    "TimeoutError",
    "UnsolvableError",
    "build_solution",
    "host_for_payload",
    "new_task_id",
]
