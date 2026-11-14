"""Admin surface. No auth yet — bind on localhost or front with an nginx auth_request.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import ORJSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from captcha.adapters.storage.models import Task as DbTask
from captcha.services.gateway.deps import get_db

router = APIRouter(prefix="/admin", tags=["admin"], default_response_class=ORJSONResponse)


@router.get("/stats")
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


@router.get("/solvers")
async def solvers() -> ORJSONResponse:
    """Registered solvers via importlib.metadata entry_points.

    Lists what's installed in the current venv — not what any worker instance
    is running. For per-worker runtime state, point an admin at the worker's
    own /debug surface.
    """
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
                    "browser": info.browser,
                    "needs_js_runtime": info.needs_js_runtime,
                    "needs_gpu": info.needs_gpu,
                    "experimental": info.experimental,
                    "version": info.version,
                }
            )
        return ORJSONResponse({"solvers": rows, "count": len(rows)})
    except Exception as exc:  # noqa: BLE001
        return ORJSONResponse({"solvers": [], "count": 0, "error": str(exc)})
