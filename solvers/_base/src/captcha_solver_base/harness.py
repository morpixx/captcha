from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from captcha_common import CaptchaType, SolveMode, Task, new_task_id

from captcha_solver_base.solver import Solver, SolverContext


@dataclass
class SolverFixture:
    name: str
    type: CaptchaType
    payload: dict[str, Any]
    expected: dict[str, Any] = field(default_factory=dict)
    timeout_sec: float = 30.0


async def run_fixtures(
    solver: Solver,
    fixtures: list[SolverFixture],
    ctx: SolverContext | None = None,
) -> list[dict[str, Any]]:
    """Run a list of fixtures against a solver, return pass/fail report.

    Used by `pytest` in each solver package and by the canary bot.
    """
    ctx = ctx or SolverContext()
    await solver.prepare(ctx)
    results = []
    try:
        for f in fixtures:
            task = Task(
                id=new_task_id(),
                type=f.type,
                payload=f.payload,
                preferred_mode=SolveMode.HTTP,
                created_at=0.0,
            )
            started = time.perf_counter()
            entry: dict[str, Any] = {"fixture": f.name, "type": f.type.value}
            try:
                result = await solver.solve_http(task, ctx)
                entry["ok"] = True
                entry["latency_ms"] = (time.perf_counter() - started) * 1000
                entry["result"] = {
                    "token": result.token,
                    "text": result.text,
                    "offset": result.offset,
                    "angle": result.angle,
                    "indices": result.indices,
                }
                if f.expected:
                    for k, v in f.expected.items():
                        actual = getattr(result, k, None)
                        if actual != v:
                            entry["ok"] = False
                            entry.setdefault("mismatches", {})[k] = {"expected": v, "actual": actual}
            except Exception as exc:  # noqa: BLE001
                entry["ok"] = False
                entry["error"] = {"type": type(exc).__name__, "message": str(exc)}
                entry["latency_ms"] = (time.perf_counter() - started) * 1000
            results.append(entry)
    finally:
        await solver.shutdown()
    return results


def load_fixtures_dir(root: Path) -> list[SolverFixture]:
    """Load fixtures from a directory of JSON files, each file one fixture."""
    import json

    out: list[SolverFixture] = []
    for p in sorted(root.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        out.append(
            SolverFixture(
                name=p.stem,
                type=CaptchaType(data["type"]),
                payload=data["payload"],
                expected=data.get("expected", {}),
                timeout_sec=data.get("timeout_sec", 30.0),
            )
        )
    return out
