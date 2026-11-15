"""Measure the Camoufox session process-tree RSS against the ~700 MB budget."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))


def tree_rss_mb() -> tuple[float, list[dict[str, float | int | str]]]:
    # The host Python process is the probe, not the browser session; count
    # only the child tree Camoufox spawned (Firefox + its content processes).
    parent = psutil.Process()
    procs = parent.children(recursive=True)
    rows = []
    total = 0
    for proc in procs:
        try:
            rss = proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        total += rss
        rows.append({"pid": proc.pid, "name": proc.name(), "rss_mb": round(rss / 1024 / 1024, 1)})
    return round(total / 1024 / 1024, 1), rows


async def main() -> int:
    from captcha.adapters.browser.provider import CamoufoxProvider
    from captcha.domain.ports import BrowserSpec

    async with (
        CamoufoxProvider(headless=True) as provider,
        provider.session(BrowserSpec(label="memory-probe", headless=True)) as session,
    ):
        page = await session.page()
        await asyncio.sleep(2.0)
        base_mb, base_rows = tree_rss_mb()
        await page.goto("https://example.com/", wait_until="domcontentloaded", timeout=60_000)
        await asyncio.sleep(1.0)
        loaded_mb, loaded_rows = tree_rss_mb()
        print(json.dumps({
            "base_mb": base_mb,
            "loaded_mb": loaded_mb,
            "base_processes": len(base_rows),
            "loaded_processes": len(loaded_rows),
            "budget_mb": 700,
            "pass": loaded_mb <= 700,
            "top": sorted(loaded_rows, key=lambda row: row["rss_mb"], reverse=True)[:8],
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
