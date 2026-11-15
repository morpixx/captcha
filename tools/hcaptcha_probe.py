"""Live hCaptcha probe: sitekey + host -> checksiteconfig -> n-token -> getcaptcha.

The instrument behind docs/hcaptcha-sites.md. It runs the same three wire calls
the HTTP solver runs (captcha.solvers.hcaptcha.wire) against the same Node
bundle worker the solver talks to (adapters.hcaptcha_js.JsBundleRunner), and
reports what came back - a pass token, an image challenge, a rejected n-token,
or a transport/protocol error - with per-stage latency.

    python tools/hcaptcha_probe.py probe --runs 10 --out tools/probe-hcaptcha.json
    python tools/hcaptcha_probe.py probe --site store.steampowered.com=<sitekey> --runs 5
    python tools/hcaptcha_probe.py discover --url https://store.steampowered.com/join/

Node needs jsdom + canvas. Install once per checkout:

    cd src/captcha/adapters/hcaptcha_js && npm ci

or lend them read-only from an existing tree with --node-path D:/riot-research/node_modules.

One deliberate choice: every run gets a fresh HTTP client. The transport is
session-per-task, and sharing one session across runs would let hCaptcha pin a
single TLS/cookie state and inflate the pass rate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER_DIR = REPO_ROOT / "src" / "captcha" / "adapters" / "hcaptcha_js"
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from captcha.adapters.hcaptcha_js import JsBundleRunner  # noqa: E402
from captcha.adapters.transport import Client  # noqa: E402
from captcha.domain.errors import CaptchaError  # noqa: E402
from captcha.solvers.hcaptcha import wire  # noqa: E402

NODE_CANDIDATES = (
    r"C:\Program Files\nodejs\node.exe",
    r"C:\Program Files (x86)\nodejs\node.exe",
    "/usr/bin/node",
    "/usr/local/bin/node",
)
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SITEKEY_RE = re.compile(r"sitekey[\"']?\s*[:=]\s*[\"']([0-9a-f-]{36})[\"']", re.I)
SCRIPT_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)[\"']", re.I)


@dataclass(frozen=True, slots=True)
class Site:
    """One deployment to measure: where it runs, which key it uses, which page it is on."""

    name: str
    host: str
    sitekey: str
    page: str


#: Captured live on 2026-11-15; provenance and verdicts live in docs/hcaptcha-sites.md.
DEFAULT_SITES: tuple[Site, ...] = (
    Site(
        name="steam-join",
        host="store.steampowered.com",
        sitekey="e18a349a-46c2-46a0-87a8-74be79345c92",
        page="https://store.steampowered.com/join/",
    ),
    Site(
        name="steam-community",
        host="steamcommunity.com",
        sitekey="e18a349a-46c2-46a0-87a8-74be79345c92",
        page="https://steamcommunity.com/login/home/",
    ),
    Site(
        name="riot-account",
        host="account.riotgames.com",
        sitekey="b3c619fc-4b72-4838-9bf2-398888588d62",
        page="https://account.riotgames.com/",
    ),
    Site(
        name="riot-authenticate",
        host="authenticate.riotgames.com",
        sitekey="a5f74b19-9e45-40e0-b45d-47ff91b7a6c2",
        page="https://authenticate.riotgames.com/",
    ),
    # Control: hCaptcha's documented always-pass test key. It answers
    # checksiteconfig with {"pass": true} and no challenge, so it measures the
    # transport + protocol only, with no fingerprint check in the way.
    Site(
        name="hcaptcha-test-key",
        host="example.com",
        sitekey="10000000-ffff-ffff-ffff-000000000001",
        page="https://example.com/",
    ),
)


class ProbeError(RuntimeError):
    """Setup or protocol failure that stops the probe before it can measure."""


@dataclass(slots=True)
class Run:
    index: int
    outcome: str = "error"
    challenge_type: str | None = None
    features: list[str] = field(default_factory=list)
    encrypted_request: bool = False
    n_len: int = 0
    token_len: int = 0
    error_codes: list[str] = field(default_factory=list)
    error: str | None = None
    cfg_ms: float = 0.0
    n_ms: float = 0.0
    get_ms: float = 0.0

    @property
    def total_ms(self) -> float:
        return self.cfg_ms + self.n_ms + self.get_ms


async def measure(site: Site, index: int, runner: JsBundleRunner, args: argparse.Namespace) -> Run:
    run = Run(index=index)
    params = wire.Params(
        sitekey=site.sitekey,
        host=site.host,
        href=site.page,
        rqdata=None,
        user_agent=args.user_agent,
        version=args.client_version,
        vm_data=args.vm_data,
    )
    client = Client(impersonate=args.impersonate, timeout=args.timeout)
    try:
        async with client:
            started = time.monotonic()
            config = await wire.checksite_config(client, params)
            run.features = sorted(config.features)
            run.encrypted_request = bool(config.features.get("enc_get_req"))
            run.cfg_ms = _ms(started)

            if config.passive:
                run.challenge_type = "passive"
                started = time.monotonic()
                data = await wire.get_captcha_passive(client, params)
                run.get_ms = _ms(started)
            else:
                challenge = config.challenge or {}
                kind = challenge.get("type")
                run.challenge_type = kind if isinstance(kind, str) else None
                req = challenge.get("req")
                if not isinstance(req, str) or not req:
                    raise ProbeError("checksiteconfig returned no challenge JWT")

                started = time.monotonic()
                n_token = await runner.solve(
                    req=req, host=site.host, href=site.page, config=params.bundle_config(), vm_data=params.vm_data
                )
                run.n_len = len(n_token)
                run.n_ms = _ms(started)
                if not n_token:
                    raise ProbeError("bundle produced an empty n-token")

                started = time.monotonic()
                data = await wire.get_captcha(client, params, challenge, config.features, n_token, req, runner)
                run.get_ms = _ms(started)

        token = wire.pass_token(data)
        if token is not None:
            run.outcome = "pass"
            run.token_len = len(token)
        elif wire.is_challenge(data):
            run.outcome = "challenge"
            run.challenge_type = wire.challenge_kind(data) or run.challenge_type
        else:
            run.outcome = "reject"
            codes = data.get("error-codes")
            run.error_codes = [str(code) for code in codes][:6] if isinstance(codes, list) else []
    except (CaptchaError, ProbeError) as exc:
        run.error = f"{type(exc).__name__}: {exc}"[:300]
    except Exception as exc:  # a live probe records a failure, it does not crash
        run.error = f"{type(exc).__name__}: {exc}"[:300]
    return run


def summarize(runs: list[Run]) -> dict[str, Any]:
    outcomes = {
        name: sum(1 for run in runs if run.outcome == name) for name in ("pass", "challenge", "reject", "error")
    }
    total = len(runs) or 1
    return {
        "runs": len(runs),
        **outcomes,
        "pass_rate": round(outcomes["pass"] / total, 4),
        "cfg_ms_p50": _pct([run.cfg_ms for run in runs], 50),
        "n_ms_p50": _pct([run.n_ms for run in runs], 50),
        "get_ms_p50": _pct([run.get_ms for run in runs], 50),
        "total_ms_p50": _pct([run.total_ms for run in runs], 50),
        "total_ms_p95": _pct([run.total_ms for run in runs], 95),
        "total_ms_mean": round(statistics.fmean([run.total_ms for run in runs]), 1) if runs else 0.0,
    }


async def probe(sites: list[Site], args: argparse.Namespace) -> dict[str, Any]:
    node_binary = args.node or _resolve_node()
    _apply_node_path(args.node_path)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "client_version": args.client_version,
        "user_agent": args.user_agent,
        "impersonate": args.impersonate,
        "node": node_binary,
        "node_path": [str(path) for path in args.node_path],
        "sites": [],
    }
    runner = JsBundleRunner(node_binary=node_binary, timeout=args.bundle_timeout)
    async with runner:
        for site in sites:
            runs: list[Run] = []
            for index in range(1, args.runs + 1):
                run = await measure(site, index, runner, args)
                runs.append(run)
                suffix = f" - {run.error}" if run.error else ""
                print(f"  {site.name} #{index}: {run.outcome}{suffix} ({run.total_ms:.0f} ms)", flush=True)
                if args.interval and index < args.runs:
                    await asyncio.sleep(args.interval)
            report["sites"].append(
                {
                    "name": site.name,
                    "host": site.host,
                    "sitekey": site.sitekey,
                    "page": site.page,
                    "runs": [asdict(run) for run in runs],
                    "summary": summarize(runs),
                }
            )
    return report


async def discover(urls: list[str], args: argparse.Namespace) -> None:
    async with Client(impersonate=args.impersonate, timeout=args.timeout) as client:
        for url in urls:
            try:
                page = await client.get(url, headers={"User-Agent": args.user_agent, "Accept": "text/html"})
            except CaptchaError as exc:
                print(f"{url}: fetch failed - {exc}")
                continue
            text = page.text
            scripts = [urljoin(url, src) for src in SCRIPT_RE.findall(text)]
            api = [src for src in scripts if "/1/api.js" in src]
            print(f"\n== {url} -> HTTP {page.status}, {len(text)} bytes, {len(scripts)} scripts")
            print(f"   hcaptcha api.js: {api or 'not on this page'}")
            for hit in sorted(set(SITEKEY_RE.findall(text))):
                print(f"   inline sitekey: {hit}")
            uuids = sorted(set(UUID_RE.findall(text)))
            if uuids:
                print(f"   inline uuids: {', '.join(uuids[:8])}")
            for script in scripts[: args.max_scripts]:
                try:
                    body = (await client.get(script, headers={"User-Agent": args.user_agent})).text
                except CaptchaError:
                    continue
                keys = sorted(set(SITEKEY_RE.findall(body)))
                if keys:
                    print(f"   sitekey in {script.rsplit('/', 1)[-1]}: {', '.join(keys)}")
            print("   (a key absent from the page and its scripts is served by an XHR - see docs/hcaptcha-sites.md)")


def parse_site(value: str) -> Site:
    host, _, sitekey = value.partition("=")
    host = host.strip().lower()
    sitekey = sitekey.strip()
    if not host or not UUID_RE.fullmatch(sitekey):
        raise argparse.ArgumentTypeError("expected HOST=<uuid sitekey>")
    return Site(name=host, host=host, sitekey=sitekey, page=f"https://{host}/")


def _resolve_node() -> str:
    found = shutil.which("node")
    if found:
        return found
    for candidate in NODE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise ProbeError("Node.js not found: install Node 20+ or pass --node")


def _apply_node_path(paths: list[Path]) -> None:
    """Lend jsdom/canvas from another checkout (node_modules is not vendored here)."""
    if not paths:
        return
    entries = [str(path) for path in paths]
    existing = os.environ.get("NODE_PATH")
    if existing:
        entries.extend(part for part in existing.split(os.pathsep) if part)
    os.environ["NODE_PATH"] = os.pathsep.join(entries)


def _ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 1)


def _pct(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, (percentile * len(ordered) + 99) // 100 - 1))
    return round(ordered[index], 1)


def _vm_data(path: str | None) -> str | None:
    if not path:
        return None
    raw = Path(path).read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw.strip() or None
    return parsed.get("vm_data") if isinstance(parsed, dict) else raw.strip()


def print_table(report: dict[str, Any]) -> None:
    header = (
        f"{'site':22} {'host':26} {'runs':>4} {'pass':>4} {'chall':>5} {'rej':>3} {'err':>3} "
        f"{'pass%':>6} {'p50 ms':>7} {'p95 ms':>7}"
    )
    print("\n" + header)
    print("-" * len(header))
    for site in report["sites"]:
        summary = site["summary"]
        print(
            f"{site['name']:22} {site['host']:26} {summary['runs']:>4} {summary['pass']:>4} "
            f"{summary['challenge']:>5} {summary['reject']:>3} {summary['error']:>3} "
            f"{summary['pass_rate'] * 100:>5.1f}% {summary['total_ms_p50']:>7.0f} {summary['total_ms_p95']:>7.0f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live hCaptcha probe (checksiteconfig -> n-token -> getcaptcha)")
    sub = parser.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--timeout", type=float, default=30.0, help="per-request HTTP timeout, seconds")
    common.add_argument("--impersonate", default="chrome131", help="curl_cffi TLS profile")
    common.add_argument("--user-agent", default=wire.DEFAULT_UA)
    common.add_argument("--node", default=None, help="node binary (default: PATH, then Program Files)")
    common.add_argument("--node-path", action="append", default=[], help="extra NODE_PATH entry (jsdom/canvas)")

    probe_cmd = sub.add_parser("probe", parents=[common], help="run the wire probe N times per site")
    probe_cmd.add_argument("--site", action="append", type=parse_site, default=None, metavar="HOST=SITEKEY")
    probe_cmd.add_argument("--only", action="append", default=[], help="restrict the built-in site list to these names")
    probe_cmd.add_argument("--runs", type=int, default=10, help="runs per site (30-50 total is the usual measurement)")
    probe_cmd.add_argument("--interval", type=float, default=1.0, help="pause between runs, seconds")
    probe_cmd.add_argument("--bundle-timeout", type=float, default=120.0, help="n-token timeout, seconds")
    probe_cmd.add_argument("--client-version", default=wire.DEFAULT_VERSION)
    probe_cmd.add_argument("--vm-data-file", default=None, help="JSON from a vm_data collector, or a raw string")
    probe_cmd.add_argument("--out", default=None, help="write the full JSON report here")

    discover_cmd = sub.add_parser("discover", parents=[common], help="scan a public page for an hCaptcha sitekey")
    discover_cmd.add_argument("--url", action="append", required=True)
    discover_cmd.add_argument("--max-scripts", type=int, default=12)

    args = parser.parse_args(argv)
    if args.command not in {"probe", "discover"}:
        parser.print_help()
        return 2
    args.vm_data = _vm_data(getattr(args, "vm_data_file", None))

    if args.command == "discover":
        asyncio.run(discover(args.url, args))
        return 0

    sites = args.site or [site for site in DEFAULT_SITES if not args.only or site.name in args.only]
    if not sites:
        print(f"no sites selected (known: {', '.join(s.name for s in DEFAULT_SITES)})")
        return 2
    args.node_path = [Path(path) for path in args.node_path]
    try:
        report = asyncio.run(probe(sites, args))
    except ProbeError as exc:
        print(f"probe setup failed: {exc}", file=sys.stderr)
        return 1
    print_table(report)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nreport: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
