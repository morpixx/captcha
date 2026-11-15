"""Per-task HTTP context for the CV lane.

Most cv-lane types are not pure image problems: reCAPTCHA audio, GeeTest, vendor
sliders and the interactive CV vendors fetch their challenge over HTTP and then
hand bytes to a model. `SolverContext` is process-wide, but an HTTP client
cannot be — it carries the task's proxy, UA and cookie jar — so exactly like the
HTTP lane this module hands each dispatch a fresh short-lived client, and the
worker closes it when `solve_http` returns.

No browser here: a cv solver that asks for one is in the wrong lane.
`NeedsBrowserError` is only honoured by `worker_http` (contract §2), so routing a
type here means it must be able to finish over HTTP + models.
"""

from __future__ import annotations

from dataclasses import replace

from captcha.adapters.transport import Client as HTTPClient
from captcha.adapters.transport import pick_impersonate
from captcha.domain.task import Task
from captcha.solvers import SolverContext

#: Same budget as the HTTP lane's default; a solver that needs longer sets
#: `Task.deadline_ts` and the worker clamps to that.
DEFAULT_TIMEOUT_S = 30.0


class HttpFactory:
    """Builds one `Client` per task with the configured JA3/JA4 profile."""

    def __init__(self, impersonate_hint: str = "") -> None:
        self.impersonate = pick_impersonate(impersonate_hint)

    def for_task(self, task: Task) -> HTTPClient:
        return HTTPClient(
            impersonate=self.impersonate,
            proxy=task.proxy,
            timeout=DEFAULT_TIMEOUT_S,
            default_headers=task_headers(task),
        )


def task_headers(task: Task) -> dict[str, str] | None:
    headers: dict[str, str] = {}
    if task.user_agent:
        headers["User-Agent"] = task.user_agent
    if task.language:
        headers["Accept-Language"] = task.language
    return headers or None


def task_context(
    template: SolverContext,
    factory: HttpFactory,
    task: Task,
) -> tuple[SolverContext, HTTPClient]:
    """Shallow-copy the template with a fresh client bound to this task."""
    client = factory.for_task(task)
    return replace(template, http=client), client
