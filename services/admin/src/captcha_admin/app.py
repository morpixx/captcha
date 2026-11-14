"""FastAPI app. Read-only dashboard; aggregates over captcha_db + captcha_queue."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from importlib import resources
from pathlib import Path
from typing import Any, AsyncIterator

import structlog
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from captcha_common.config import get_settings
from captcha_db import SolverRun, Task, init_engine, shutdown_engine
from captcha_db.session import async_session
from captcha_observability import setup_logging

logger = structlog.get_logger(__name__)


# ---------- template + static resolution ----------

def _pkg_dir() -> Path:
    # importlib.resources.files() → the installed package directory, works both
    # from a wheel install and from `uv run` against the source tree.
    return Path(str(resources.files("captcha_admin")))


def _templates_dir() -> Path:
    return _pkg_dir() / "templates"


def _static_dir() -> Path:
    return _pkg_dir() / "static"


# ---------- lifespan ----------

@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(level=settings.log_level, json=settings.log_json)
    init_engine(settings.postgres_url, pool_size=max(4, settings.postgres_pool_size // 4))
    logger.info("admin.startup", postgres=settings.postgres_url.split("@")[-1])
    try:
        yield
    finally:
        await shutdown_engine()
        logger.info("admin.shutdown")


# ---------- aggregates ----------

async def _type_stats(session: AsyncSession, *, since: datetime) -> list[dict[str, Any]]:
    """Per-type counts + latency. One query, DB does the maths."""
    ready = func.sum(case((Task.status == "ready", 1), else_=0))
    failed = func.sum(case((Task.status == "failed", 1), else_=0))
    processing = func.sum(case((Task.status == "processing", 1), else_=0))
    pending = func.sum(case((Task.status == "pending", 1), else_=0))
    # latency = finished_at - started_at (seconds), averaged — Postgres only
    latency_s = func.avg(
        func.extract("epoch", Task.finished_at) - func.extract("epoch", Task.started_at)
    )
    stmt = (
        select(
            Task.type.label("type"),
            func.count().label("total"),
            ready.label("ready"),
            failed.label("failed"),
            processing.label("processing"),
            pending.label("pending"),
            latency_s.label("avg_latency_s"),
        )
        .where(Task.created_at >= since)
        .group_by(Task.type)
        .order_by(func.count().desc())
    )
    rows = (await session.execute(stmt)).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        total = int(r["total"] or 0)
        ok = int(r["ready"] or 0)
        bad = int(r["failed"] or 0)
        denom = ok + bad
        out.append(
            {
                "type": r["type"],
                "total": total,
                "ready": ok,
                "failed": bad,
                "processing": int(r["processing"] or 0),
                "pending": int(r["pending"] or 0),
                "success_rate": (ok / denom) if denom else 0.0,
                "avg_latency_ms": round(float(r["avg_latency_s"] or 0.0) * 1000.0, 1),
            }
        )
    return out


async def _solver_stats(session: AsyncSession, *, since: datetime) -> list[dict[str, Any]]:
    ok = func.sum(case((SolverRun.ok.is_(True), 1), else_=0))
    bad = func.sum(case((SolverRun.ok.is_(False), 1), else_=0))
    stmt = (
        select(
            SolverRun.solver_name.label("name"),
            func.count().label("runs"),
            ok.label("ok"),
            bad.label("fail"),
            func.avg(SolverRun.latency_ms).label("avg_latency_ms"),
            func.percentile_cont(0.95).within_group(SolverRun.latency_ms).label("p95_latency_ms"),
            func.max(SolverRun.started_at).label("last_run"),
        )
        .where(SolverRun.started_at >= since)
        .group_by(SolverRun.solver_name)
        .order_by(func.count().desc())
    )
    rows = (await session.execute(stmt)).mappings().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        runs = int(r["runs"] or 0)
        good = int(r["ok"] or 0)
        out.append(
            {
                "name": r["name"],
                "runs": runs,
                "ok": good,
                "fail": int(r["fail"] or 0),
                "success_rate": (good / runs) if runs else 0.0,
                "avg_latency_ms": round(float(r["avg_latency_ms"] or 0.0), 1),
                "p95_latency_ms": round(float(r["p95_latency_ms"] or 0.0), 1),
                "last_run": r["last_run"].isoformat() if r["last_run"] else None,
            }
        )
    return out


async def _solver_detail(
    session: AsyncSession, name: str, *, since: datetime, limit: int = 100
) -> dict[str, Any]:
    # per-task-type break-down
    ok = func.sum(case((SolverRun.ok.is_(True), 1), else_=0))
    bad = func.sum(case((SolverRun.ok.is_(False), 1), else_=0))
    per_type_stmt = (
        select(
            SolverRun.task_type.label("task_type"),
            func.count().label("runs"),
            ok.label("ok"),
            bad.label("fail"),
            func.avg(SolverRun.latency_ms).label("avg_latency_ms"),
        )
        .where(SolverRun.solver_name == name, SolverRun.started_at >= since)
        .group_by(SolverRun.task_type)
        .order_by(func.count().desc())
    )
    per_type = [dict(r) for r in (await session.execute(per_type_stmt)).mappings().all()]

    err_stmt = (
        select(
            SolverRun.error_code.label("code"),
            func.count().label("n"),
        )
        .where(
            SolverRun.solver_name == name,
            SolverRun.started_at >= since,
            SolverRun.ok.is_(False),
            SolverRun.error_code.is_not(None),
        )
        .group_by(SolverRun.error_code)
        .order_by(func.count().desc())
        .limit(20)
    )
    errors = [{"code": r["code"], "n": int(r["n"])} for r in (await session.execute(err_stmt)).mappings().all()]

    recent_stmt = (
        select(SolverRun)
        .where(SolverRun.solver_name == name)
        .order_by(SolverRun.started_at.desc())
        .limit(limit)
    )
    recent = [
        {
            "id": r.id,
            "task_type": r.task_type,
            "ok": r.ok,
            "latency_ms": r.latency_ms,
            "error_code": r.error_code,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "task_id": str(r.task_id) if r.task_id else None,
        }
        for r in (await session.execute(recent_stmt)).scalars().all()
    ]

    return {"name": name, "per_type": per_type, "errors": errors, "recent": recent}


async def _recent_tasks(
    session: AsyncSession,
    *,
    status: str | None,
    type_: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    stmt = select(Task).order_by(Task.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Task.status == status)
    if type_:
        stmt = stmt.where(Task.type == type_)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": str(t.id),
            "type": t.type,
            "status": t.status,
            "retries": t.retries,
            "worker_id": t.worker_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
            "latency_ms": (
                round((t.finished_at - t.started_at).total_seconds() * 1000.0, 1)
                if t.finished_at and t.started_at
                else None
            ),
            "error_code": (t.error or {}).get("code") if t.error else None,
        }
        for t in rows
    ]


# ---------- app factory ----------

def build_app() -> FastAPI:
    app = FastAPI(
        title="captcha-admin",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    templates = Jinja2Templates(directory=str(_templates_dir()))
    app.mount("/static", StaticFiles(directory=str(_static_dir())), name="static")

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, window: str = "24h") -> HTMLResponse:
        since = _since(window)
        async with async_session() as s:
            types = await _type_stats(s, since=since)
            solvers = await _solver_stats(s, since=since)
        totals = {
            "total": sum(t["total"] for t in types),
            "ready": sum(t["ready"] for t in types),
            "failed": sum(t["failed"] for t in types),
            "processing": sum(t["processing"] for t in types),
            "pending": sum(t["pending"] for t in types),
        }
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "types": types,
                "solvers": solvers[:10],
                "totals": totals,
                "window": window,
            },
        )

    @app.get("/solvers", response_class=HTMLResponse)
    async def solvers(request: Request, window: str = "24h") -> HTMLResponse:
        since = _since(window)
        async with async_session() as s:
            rows = await _solver_stats(s, since=since)
        return templates.TemplateResponse(
            request, "solvers.html", {"solvers": rows, "window": window}
        )

    @app.get("/solvers/{name}", response_class=HTMLResponse)
    async def solver_detail(request: Request, name: str, window: str = "7d") -> HTMLResponse:
        since = _since(window)
        async with async_session() as s:
            data = await _solver_detail(s, name, since=since)
        return templates.TemplateResponse(
            request, "solver_detail.html", {"solver": data, "window": window}
        )

    @app.get("/tasks", response_class=HTMLResponse)
    async def tasks(
        request: Request,
        status: str | None = None,
        type: str | None = Query(default=None, alias="type"),
        limit: int = 200,
    ) -> HTMLResponse:
        limit = max(10, min(1000, limit))
        async with async_session() as s:
            rows = await _recent_tasks(s, status=status, type_=type, limit=limit)
        return templates.TemplateResponse(
            request,
            "tasks.html",
            {"tasks": rows, "status": status or "", "type": type or "", "limit": limit},
        )

    @app.get("/proxies", response_class=HTMLResponse)
    async def proxies(request: Request) -> HTMLResponse:
        # FILL: wire this to captcha_proxy.pool stats once the package exposes a
        # read-only snapshot (success/failure counters per host). For now we render
        # whatever's in the configured pool file.
        settings = get_settings()
        entries: list[dict[str, Any]] = []
        pool_path = Path(settings.proxy_pool_file)
        if pool_path.exists():
            for i, line in enumerate(pool_path.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                entries.append({"idx": i, "proxy": _redact_proxy(line)})
        return templates.TemplateResponse(
            request,
            "proxies.html",
            {"proxies": entries, "pool_file": str(pool_path)},
        )

    # JSON API — same aggregates, machine-readable (for grafana-style scraping)

    @app.get("/api/types")
    async def api_types(window: str = "24h") -> JSONResponse:
        since = _since(window)
        async with async_session() as s:
            return JSONResponse(await _type_stats(s, since=since))

    @app.get("/api/solvers")
    async def api_solvers(window: str = "24h") -> JSONResponse:
        since = _since(window)
        async with async_session() as s:
            return JSONResponse(await _solver_stats(s, since=since))

    return app


def _since(window: str) -> datetime:
    now = datetime.now(timezone.utc)
    w = window.lower().strip()
    if w.endswith("h"):
        return now - timedelta(hours=_int(w[:-1], 24))
    if w.endswith("d"):
        return now - timedelta(days=_int(w[:-1], 1))
    if w.endswith("m"):
        return now - timedelta(minutes=_int(w[:-1], 60))
    return now - timedelta(hours=24)


def _int(s: str, default: int) -> int:
    try:
        return max(1, int(s))
    except ValueError:
        return default


def _redact_proxy(s: str) -> str:
    # strip userinfo — don't leak passwords into HTML
    # formats: scheme://user:pass@host:port  |  user:pass@host:port  |  host:port
    sep = s.rsplit("@", 1)
    if len(sep) == 2:
        return f"***@{sep[1]}"
    return s


app = build_app()
