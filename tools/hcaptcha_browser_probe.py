"""Live hCaptcha browser-lane probe: Camoufox on the real target origin.

Riot and Steam sitekeys refuse the synthetic HTTP n-token; this probe drives the
same CamoufoxProvider + HCaptchaBrowserSolver the browser worker uses, on the
page where the token is consumed. It never submits a form or changes an account.

    python tools/hcaptcha_browser_probe.py --target riot --runs 1
    python tools/hcaptcha_browser_probe.py --target steam --runs 1
    python tools/hcaptcha_browser_probe.py --target control --runs 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

TARGETS: dict[str, dict[str, str]] = {
    "riot": {
        "url": "https://account.riotgames.com/",
        "sitekey": "b3c619fc-4b72-4838-9bf2-398888588d62",
    },
    "riot-auth": {
        "url": "https://authenticate.riotgames.com/",
        "sitekey": "a5f74b19-9e45-40e0-b45d-47ff91b7a6c2",
    },
    "steam": {
        "url": "https://store.steampowered.com/join/",
        "sitekey": "e18a349a-46c2-46a0-87a8-74be79345c92",
    },
    "control": {
        "url": "https://example.com/",
        "sitekey": "10000000-ffff-ffff-ffff-000000000001",
    },
}


@dataclass
class Run:
    target: str
    status: str
    token_prefix: str | None
    error: str | None
    problem: str | None
    latency_ms: float


async def one_run(target: str) -> Run:
    from captcha.adapters.browser.provider import CamoufoxProvider
    from captcha.domain import CaptchaType, Task
    from captcha.domain.errors import CaptchaError
    from captcha.domain.task import new_task_id
    from captcha.solvers import SolverContext
    from captcha.solvers.hcaptcha.browser import HCaptchaBrowserSolver

    cfg = TARGETS[target]
    async with CamoufoxProvider(headless=True) as provider:
        task = Task(
            id=new_task_id(),
            type=CaptchaType.HCAPTCHA,
            payload={"websiteURL": cfg["url"], "websiteKey": cfg["sitekey"]},
            sitekey=cfg["sitekey"],
            site_url=cfg["url"],
        )
        ctx = SolverContext(
            browser=provider,
            config=SimpleNamespace(worker_browser_headless=True),
        )
        started = time.perf_counter()
        try:
            result = await HCaptchaBrowserSolver().solve_browser(task, ctx)
            return Run(
                target=target,
                status="pass",
                token_prefix=(result.token or "")[:12],
                error=None,
                problem=None,
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except CaptchaError as exc:
            return Run(
                target=target,
                status="fail",
                token_prefix=None,
                error=exc.code,
                problem=str(exc.context.get("problem") or exc.context.get("reason") or exc.context.get("error") or ""),
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Live hCaptcha browser probe")
    parser.add_argument("--target", choices=sorted(TARGETS), default="riot")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    rows: list[Run] = []
    for index in range(args.runs):
        row = await one_run(args.target)
        rows.append(row)
        print(
            f"[{args.target}] {index + 1}/{args.runs} {row.status} "
            f"{row.latency_ms:.0f}ms token={row.token_prefix or '-'} "
            f"err={row.error or '-'} problem={row.problem or '-'}"
        )
    report = {
        "ts": datetime.now(UTC).isoformat(),
        "target": args.target,
        "runs": [asdict(row) for row in rows],
        "pass_rate": sum(row.status == "pass" for row in rows) / max(1, len(rows)),
    }
    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0 if any(row.status == "pass" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
