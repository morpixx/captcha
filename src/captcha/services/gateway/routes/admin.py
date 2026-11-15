"""Admin surface: cheap DB/registry rollups. Off unless ADMIN_TOKEN is set.

No session auth, no roles — one shared bearer-ish token in a header is enough
for an internal read-only endpoint. Empty token means the routes answer 404,
so a default deployment does not expose anything at all.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import ORJSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.adapters.storage.models import Task as DbTask
from captcha.services.gateway.deps import get_db, get_state
from captcha.services.gateway.state import AppState

router = APIRouter(prefix="/admin", tags=["admin"], default_response_class=ORJSONResponse)


def require_admin(
    state: AppState = Depends(get_state),
    x_admin_token: str | None = Header(default=None),
) -> None:
    """404 when the surface is disabled, 401 on a wrong/missing token."""
    expected = state.settings.admin_token
    if not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bad admin token")


@router.get("/stats", dependencies=[Depends(require_admin)])
async def stats(session: AsyncSession = Depends(get_db)) -> ORJSONResponse:
    """Aggregate task counts by status.

    Cheap single-query rollup; indexed on `status`, so the planner uses
    `ix_tasks_status_created`. Over the lifetime of the DB this gets large —
    swap for a materialized view once that matters.
    """
    stmt = select(DbTask.status, func.count()).group_by(DbTask.status)
    rows = (await session.execute(stmt)).all()
    out: dict[str, int] = {row[0]: int(row[1]) for row in rows}
    for s in ("pending", "processing", "ready", "failed"):
        out.setdefault(s, 0)
    return ORJSONResponse(out)


@router.get("/solvers", dependencies=[Depends(require_admin)])
async def solvers() -> ORJSONResponse:
    """Solvers registered in this process (importable in this venv)."""
    try:
        from captcha.solvers.registry import discover_solvers

        reg = discover_solvers()
        rows: list[dict[str, object]] = []
        for cls in reg.all_solvers():
            info = cls.info
            rows.append(
                {
                    "name": info.name,
                    "types": [t.value for t in info.types],
                    "http": info.http,
                    "needs_js_runtime": info.needs_js_runtime,
                    "needs_gpu": info.needs_gpu,
                    "experimental": info.experimental,
                    "version": info.version,
                }
            )
        return ORJSONResponse({"solvers": rows, "count": len(rows)})
    except Exception as exc:
        return ORJSONResponse({"solvers": [], "count": 0, "error": str(exc)})
