"""Live Cloudflare Turnstile probe: CamoufoxProvider + TurnstileSolver.solve_browser.

The instrument behind the Turnstile L3 push. It opens one fresh Camoufox
session per run (same provider the browser lane uses in production), navigates
to a target page that embeds the widget, drives the widget through
`TurnstileSolver.solve_browser`, and reports per-run outcome plus pass rate,
p50/p95 latency and the error-code distribution.

    python tools/turnstile_probe.py --scenario visible-pass --runs 10
    python tools/turnstile_probe.py --scenario all --runs 5 --out tools/probe-turnstile.json
    python tools/turnstile_probe.py --target https://demo.turnstile.workers.dev \
                                    --sitekey 1x00000000000000000000AA --runs 10

Three modes:

  * `--scenario <name>` (default): loopback harness on 127.0.0.1 with one of
    Cloudflare's documented test sitekeys. Deterministic baseline — a visible-
    pass sitekey must mint a token every time, visible-block must never, and
    an interactive sitekey must trip the keyboard-nudge path.
  * `--scenario all`: all six scenarios back to back.
  * `--target <url> --sitekey <key>`: real deployment. The sitekey the widget
    uses on that page; the probe trusts the host, same as production.

The loopback harness binds `127.0.0.1:<port>` and serves one tiny HTML page
with `<script src=challenges.cloudflare.com/turnstile/v0/api.js>` plus the
`.cf-turnstile` div. `adapters.netguard` blocks private IPs by design: the
probe patches it out for its own process only. A real target goes through the
full netguard check unchanged.

Camoufox is not installed by default; install the browser extra before running:

    uv pip install camoufox psutil
    python -m camoufox fetch

The import is lazy so `--help` works without it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import socket
import statistics
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))


#: Cloudflare's documented test sitekeys
#: (developers.cloudflare.com/turnstile/troubleshooting/testing/). Every one is
#: anchored to the host that loads the widget, so they work on 127.0.0.1 just
#: as well as on a real domain.
TEST_SITEKEYS: dict[str, dict[str, Any]] = {
    "visible-pass": {"sitekey": "1x00000000000000000000AA", "invisible": False, "expect": "pass"},
    "visible-block": {"sitekey": "2x00000000000000000000AB", "invisible": False, "expect": "block"},
    "visible-interactive": {"sitekey": "3x00000000000000000000FF", "invisible": False, "expect": "interactive"},
    "invisible-pass": {"sitekey": "1x00000000000000000000BB", "invisible": True, "expect": "pass"},
    "invisible-block": {"sitekey": "2x00000000000000000000BB", "invisible": True, "expect": "block"},
    "invisible-interactive": {"sitekey": "3x00000000000000000000BB", "invisible": True, "expect": "interactive"},
}

LOCAL_HARNESS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Turnstile probe</title></head>
<body>
<h1>Turnstile probe harness</h1>
<form>
  <div class="cf-turnstile" data-sitekey="{sitekey}"{invisible_attr}></div>
  <input name="cf-turnstile-response" value="">
</form>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
</body></html>
"""


@dataclass
class RunResult:
    scenario: str
    sitekey: str
    invisible: bool
    status: str  # "pass" | "fail"
    token_prefix: str | None = None
    error_type: str | None = None
    error_code: str | None = None
    latency_ms: float = 0.0


def _harness_handler(html: str) -> type[BaseHTTPRequestHandler]:
    payload = html.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet
            return

    return Handler


@contextlib.contextmanager
def harness_server(sitekey: str, invisible: bool):
    inv_attr = ' data-appearance="execute"' if invisible else ""
    html = LOCAL_HARNESS_HTML.format(sitekey=sitekey, invisible_attr=inv_attr)
    # Bind to a free loopback port.
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _harness_handler(html))
    host, port = srv.server_address
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        # Use the resolver's AF_INET result so the URL uses the same form the
        # browser tries to connect to.
        _ = socket.gethostbyname("127.0.0.1")
        yield f"http://{host}:{port}/"
    finally:
        srv.shutdown()
        srv.server_close()


def _patch_netguard_for_loopback() -> None:
    """Allow 127.0.0.1 inside this process so the loopback harness works.

    Production paths are untouched: netguard still blocks private targets for
    anything that imports it after this call does not help, so we also patch
    the symbol the browser provider already imported.
    """
    from captcha.adapters import netguard

    async def _noop_async(url: str, *, resolver: Any = None, field: str = "url") -> str:
        return url

    def _noop(url: str, *, resolver: Any = None, field: str = "url") -> str:
        return url

    def _noop_shape(url: str, *, field: str = "url") -> None:
        return None

    netguard.ensure_public_url_async = _noop_async  # type: ignore[assignment]
    netguard.ensure_public_url_shape = _noop_shape  # type: ignore[assignment]
    netguard.ensure_public_url = _noop  # type: ignore[assignment]

    from captcha.adapters.browser import provider

    provider.ensure_public_url = _noop  # type: ignore[attr-defined,assignment]


class _MinimalConfig:
    """Just enough shape for `_common.spec_for`."""

    worker_browser_headless = True


async def one_run(provider: Any, scenario: str, cfg: dict[str, Any], site_url: str) -> RunResult:
    from captcha.domain import CaptchaType, Task
    from captcha.domain.errors import CaptchaError
    from captcha.domain.task import new_task_id
    from captcha.solvers import SolverContext
    from captcha.solvers.antibot.cloudflare.turnstile import TurnstileSolver

    task = Task(
        id=new_task_id(),
        type=CaptchaType.TURNSTILE_INVISIBLE if cfg["invisible"] else CaptchaType.TURNSTILE,
        payload={"websiteURL": site_url, "websiteKey": cfg["sitekey"]},
        sitekey=cfg["sitekey"],
        site_url=site_url,
    )
    ctx = SolverContext(browser=provider, config=_MinimalConfig())
    solver = TurnstileSolver()
    started = time.perf_counter()
    try:
        result = await solver.solve_browser(task, ctx)
        token = result.token or ""
        return RunResult(
            scenario=scenario,
            sitekey=cfg["sitekey"],
            invisible=cfg["invisible"],
            status="pass",
            token_prefix=token[:12],
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
    except CaptchaError as exc:
        err_code = ""
        if exc.context:
            err_code = str(exc.context.get("error") or exc.context.get("reason") or "")
        return RunResult(
            scenario=scenario,
            sitekey=cfg["sitekey"],
            invisible=cfg["invisible"],
            status="fail",
            error_type=exc.code,
            error_code=err_code or None,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )


def summarize(results: list[RunResult]) -> dict[str, Any]:
    passed = [r for r in results if r.status == "pass"]
    failed = [r for r in results if r.status == "fail"]
    latencies = [r.latency_ms for r in results]
    err_dist: dict[str, int] = {}
    for r in failed:
        key = r.error_type or r.error_code or "unknown"
        err_dist[key] = err_dist.get(key, 0) + 1

    def pct(xs: list[float], q: float) -> float:
        if not xs:
            return 0.0
        ordered = sorted(xs)
        k = max(0, min(len(ordered) - 1, int(len(ordered) * q)))
        return ordered[k]

    return {
        "runs": len(results),
        "pass": len(passed),
        "fail": len(failed),
        "pass_rate": len(passed) / max(1, len(results)),
        "p50_ms": statistics.median(latencies) if latencies else 0.0,
        "p95_ms": pct(latencies, 0.95),
        "errors": err_dist,
        "token_sample": passed[0].token_prefix if passed else None,
    }


@contextlib.asynccontextmanager
async def _open_provider(*, headless: bool):
    from captcha.adapters.browser.provider import CamoufoxProvider

    async with CamoufoxProvider(headless=headless) as provider:
        yield provider


async def _run_scenario(
    name: str,
    cfg: dict[str, Any],
    *,
    runs: int,
    headless: bool,
    target: str | None,
) -> list[RunResult]:
    results: list[RunResult] = []
    stack = contextlib.AsyncExitStack()
    async with stack:
        site_url = stack.enter_context(harness_server(cfg["sitekey"], cfg["invisible"])) if target is None else target
        provider = await stack.enter_async_context(_open_provider(headless=headless))
        for i in range(runs):
            r = await one_run(provider, name, cfg, site_url)
            results.append(r)
            print(
                f"[{name}] {i + 1}/{runs} {r.status} {r.latency_ms:.0f}ms "
                f"token={r.token_prefix or '-'} err={r.error_type or '-'}"
            )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live Cloudflare Turnstile probe")
    parser.add_argument("--runs", type=int, default=10, help="runs per scenario (default 10)")
    parser.add_argument(
        "--scenario",
        choices=[*list(TEST_SITEKEYS), "all"],
        default="visible-pass",
        help="CF test-sitekey scenario",
    )
    parser.add_argument("--sitekey", help="override sitekey (requires --target)")
    parser.add_argument("--target", help="public page URL that embeds the widget")
    parser.add_argument("--invisible", action="store_true", help="with --sitekey: invisible widget")
    parser.add_argument("--headed", action="store_true", help="run Camoufox non-headless")
    parser.add_argument("--out", type=Path, help="write JSON report")
    args = parser.parse_args(argv)

    if args.sitekey and not args.target:
        print("--sitekey needs --target (the real page where the widget runs)", file=sys.stderr)
        return 2

    scenarios: list[tuple[str, dict[str, Any]]]
    if args.sitekey:
        cfg = {"sitekey": args.sitekey, "invisible": args.invisible, "expect": "pass"}
        scenarios = [("custom", cfg)]
    elif args.scenario == "all":
        scenarios = list(TEST_SITEKEYS.items())
    else:
        scenarios = [(args.scenario, TEST_SITEKEYS[args.scenario])]

    # Only patch netguard when we are actually binding the loopback harness.
    if args.target is None:
        _patch_netguard_for_loopback()

    report: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "runs_per_scenario": args.runs,
        "target": args.target,
        "scenarios": {},
    }

    async def _go() -> None:
        for name, cfg in scenarios:
            rs = await _run_scenario(
                name,
                cfg,
                runs=args.runs,
                headless=not args.headed,
                target=args.target,
            )
            report["scenarios"][name] = {
                "config": {"sitekey": cfg["sitekey"], "invisible": cfg["invisible"], "expect": cfg["expect"]},
                "results": [asdict(r) for r in rs],
                "summary": summarize(rs),
            }

    asyncio.run(_go())

    print("\n--- summary ---")
    for name, data in report["scenarios"].items():
        s = data["summary"]
        expect = data["config"]["expect"]
        print(
            f"{name:24} expect={expect:12} pass_rate={s['pass_rate']:.2f} "
            f"({s['pass']}/{s['runs']}) p50={s['p50_ms']:.0f}ms p95={s['p95_ms']:.0f}ms "
            f"errors={s['errors']}"
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
