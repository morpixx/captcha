"""Pure domain layer. stdlib only — no network, no database, no logging framework.

Everything here is a decision we own: the captcha taxonomy, task lifecycle,
error codes, prices, and the shape of a solution. Nothing in this package can
break because a vendor shipped a new build.
"""

from .catalog import (
    CATALOG,
    DEFAULT_PRICE,
    LANES,
    Lane,
    SolutionKind,
    TypeSpec,
    all_specs,
    lane_for,
    price,
    solution_kind,
    spec,
    types_by_lane,
    types_by_solution_kind,
)
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
    "CATALOG",
    "DEFAULT_PRICE",
    "LANES",
    "AntibotEngine",
    "CaptchaError",
    "CaptchaType",
    "ChallengeExpiredError",
    "ConfigError",
    "JSRuntimeError",
    "Lane",
    "ModelInferenceError",
    "Priority",
    "ProxyError",
    "SensorError",
    "SolutionKind",
    "SolveMode",
    "SolverResult",
    "Task",
    "TaskEnvelope",
    "TaskStatus",
    "TimeoutError",
    "TypeSpec",
    "UnsolvableError",
    "all_specs",
    "build_solution",
    "host_for_payload",
    "lane_for",
    "new_task_id",
    "price",
    "solution_kind",
    "spec",
    "types_by_lane",
    "types_by_solution_kind",
]
