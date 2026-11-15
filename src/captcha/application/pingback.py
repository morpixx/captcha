"""Projection of a finished task onto the 2captcha pingback payload.

2captcha POSTs the same JSON body the client would read from `getTaskResult` to
the `callbackUrl` given at create time. The shape therefore belongs in one pure
function: the worker, the gateway route, and the tests must agree on it, and the
solution field still comes from `captcha.domain.solution.build_solution` so the
per-type keys stay canonical.
"""

from __future__ import annotations

from typing import Any

import msgspec

from captcha.domain.enums import CaptchaType, TaskStatus
from captcha.domain.solution import build_solution
from captcha.domain.task import SolverResult


def pingback_payload(
    status: TaskStatus | str,
    result: SolverResult | dict[str, Any] | None,
    error: dict[str, Any] | None,
    task_type: CaptchaType | str | None,
    site_url: str | None,
    *,
    cost: str | None = None,
) -> dict[str, Any]:
    """Build the body a pingback delivers for a task that reached a terminal state.

    Mirrors `GetTaskResultResponse` serialized with `exclude_none=True`: errorId
    and status always, solution for `ready`, errorCode/errorDescription for
    `failed`, and cost only when the caller knows it.
    """
    api_status = _api_status(status)
    payload: dict[str, Any] = {"errorId": 0, "status": api_status}
    if cost is not None:
        payload["cost"] = cost

    if api_status == "ready" and result is not None:
        solution = _solution(task_type, result, site_url)
        if solution:
            payload["solution"] = solution
    if api_status == "failed" and error is not None:
        payload["errorCode"] = str(error.get("code") or "ERR_UNKNOWN")
        payload["errorDescription"] = str(error.get("message") or "")
    return payload


def _api_status(status: TaskStatus | str) -> str:
    if isinstance(status, TaskStatus):
        if status is TaskStatus.READY:
            return "ready"
        if status is TaskStatus.FAILED:
            return "failed"
        return "processing"
    value = str(status)
    if value in ("ready", "processing", "failed"):
        return value
    try:
        return _api_status(TaskStatus(value))
    except ValueError:
        return "processing"


def _solution(
    task_type: CaptchaType | str | None,
    result: SolverResult | dict[str, Any],
    site_url: str | None,
) -> dict[str, Any] | None:
    raw: dict[str, Any] = result if isinstance(result, dict) else msgspec.to_builtins(result)
    if not raw:
        return None
    captcha_type = _captcha_type(task_type)
    if captcha_type is None:
        # Type this release no longer knows — deliver the stored shape untouched.
        return raw
    return build_solution(captcha_type, raw, site_url=site_url)


def _captcha_type(task_type: CaptchaType | str | None) -> CaptchaType | None:
    if isinstance(task_type, CaptchaType):
        return task_type
    if task_type is None:
        return None
    try:
        return CaptchaType(str(task_type))
    except ValueError:
        return None
