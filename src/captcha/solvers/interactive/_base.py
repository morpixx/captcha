"""Interactive base — CV delegation helpers for vendor-specific solvers.

All interactive solvers in this package follow the same shape:
  1. HTTP init with vendor API — get challenge metadata + image URLs.
  2. Fetch challenge images via `ctx.http`.
  3. Hand bytes to a CV primitive (slider / rotate / click / bbox) via the
     solver registry, get back a geometric answer.
  4. Envelope the answer in the vendor's track/sig format, POST to verify,
     parse the vendor token.

Step 3 is the shared move and lives here. Everything else is per-vendor.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import Priority, SolveMode
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.solvers.base import Solver, SolverContext
from captcha.solvers.registry import global_registry


class InteractiveBase(Solver):
    """Shared helpers for interactive vendor solvers.

    Subclasses implement `solve_http`; the base provides CV delegation that
    hides the registry lookup, subtask construction, and error normalization.
    """

    async def _solve_cv(
        self,
        kind: CaptchaType,
        parent: Task,
        ctx: SolverContext,
        payload: dict[str, Any],
    ) -> SolverResult:
        """Dispatch an image-based subproblem to a registered CV primitive.

        `kind` is one of CaptchaType.SLIDER / ROTATE / CLICK / BOUNDING_BOX /
        CLASSIFICATION_GRID / COORDINATES. `payload` carries the CV solver's
        expected shape (e.g. {"bg": <bytes>, "piece": <bytes>} for slider).
        """
        registry = global_registry()
        cls = registry.pick(kind)
        if cls is None:
            raise UnsolvableError(
                f"no CV solver registered for {kind.value}",
                context={"parent_type": parent.type.value, "need": kind.value},
            )
        sub = Task(
            id=uuid4(),
            type=kind,
            payload=payload,
            priority=parent.priority if isinstance(parent.priority, Priority) else Priority.NORMAL,
            preferred_mode=SolveMode.HTTP,
            sitekey=parent.sitekey,
            site_url=parent.site_url,
            proxy=parent.proxy,
            user_agent=parent.user_agent,
            language=parent.language,
            created_at=parent.created_at,
            deadline_ts=parent.deadline_ts,
            client_id=parent.client_id,
            attempt=parent.attempt,
        )
        inst = cls()
        try:
            return await inst.solve_http(sub, ctx)
        except CaptchaError:
            raise
        except Exception as exc:
            raise CaptchaError(
                f"{kind.value} CV delegate crashed: {exc}",
                context={"parent_type": parent.type.value, "delegate": cls.__name__},
            ) from exc

    @staticmethod
    def _require(payload: dict[str, Any], *keys: str) -> None:
        """Validate that a vendor payload carries the fields we need upfront."""
        missing = [k for k in keys if payload.get(k) in (None, "")]
        if missing:
            raise UnsolvableError(
                f"missing payload fields: {missing}",
                context={"missing": missing, "have": sorted(payload.keys())},
            )
