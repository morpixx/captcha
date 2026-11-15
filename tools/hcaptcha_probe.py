"""Live hCaptcha probe: sitekey + host -> checksiteconfig -> n-token -> getcaptcha.

The instrument behind docs/hcaptcha-sites.md. It runs the same three wire calls
the HTTP solver runs (captcha.solvers.hcaptcha.wire) against the same Node
bundle worker (adapters/hcaptcha_js/server.js) and reports what came back - a
P1_ pass token, an image challenge, a rejected n-token, or a transport/protocol
error - with per-stage latency.

    python tools/hcaptcha_probe.py probe --runs 10 --out tools/probe-hcaptcha.json
    python tools/hcaptcha_probe.py probe --site store.steampowered.com=<sitekey> --runs 5
    python tools/hcaptcha_probe.py discover --url https://store.steampowered.com/join/

Node needs jsdom + canvas. Install once per checkout:

    cd src/captcha/adapters/hcaptcha_js && npm ci

or lend them read-only from an existing tree with --node-path D:/riot-research/node_modules.

Two deliberate choices, both documented in docs/hcaptcha-sites.md:

* one fresh HTTP client per run - the transport is session-per-task, and sharing
  one session across runs lets hCaptcha pin a single TLS/cookie state;
* the bundle worker stays persistent, but non-JSON stdout lines are dropped and
  counted. adapters/hcaptcha_js/realm.js lets the bundle's own console.log reach
  stdout, so a successful solve is trailed by a stray "undefined" line that the
  adapter's runner would read as the next response.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
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
SERVER_JS = ADAPTER_DIR / "server.js"
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

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


class BundleWorker:
    """Tolerant client for adapters/hcaptcha_js/server.js.

    Speaks the stdio protocol documented in server.js and satisfies
    wire.BundleRunner. Only the read loop differs from JsBundleRunner: a line
    that is not JSON is dropped and counted instead of failing the call.
    """

    def __init__(self, *, node_binary: str | None = None, timeout: float = 120.0, node_path: list[str] | None = None):
        self.node_binary = node_binary or _resolve_node()
        self.timeout = timeout
        self.node_path = [str(Path(p)) for p in (node_path or [])]
        self.stray: list[str] = []
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        env = dict(os.environ)
        env.setdefault("NODE_ENV", "production")
        if self.node_path:
            env["NODE_PATH"] = os.pathsep.join([*self.node_path, *filter(None, [env.get("NODE_PATH")])])
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.node_binary,
                str(SERVER_JS),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ADAPTER_DIR),
                env=env,
            )
        except OSError as exc:
            raise ProbeError(f"cannot start the bundle worker with {self.node_binary}: {exc}") from exc

    async def aclose(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, RuntimeError):
            pass
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=3)
        except (TimeoutError, ProcessLookupError):
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass

    async def __aenter__(self) -> BundleWorker:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def solve(self, *, req: str, host: str, href: str, config: dict[str, Any], vm_data: str | None = None) -> str:
        request: dict[str, Any] = {"mode": "solve", "req": req, "host": host, "href": href, "config": config}
        if vm_data is not None:
            request["vm_data"] = vm_data
        return (await self._call(request)).decode("latin1")

    async def encrypt(self, req: str, plaintext: bytes, config: dict[str, Any]) -> bytes:
        return await self._call({"mode": "encrypt", "req": req, "data": _b64(plaintext), "config": config})

    async def decrypt(self, req: str, blob: bytes, config: dict[str, Any]) -> bytes:
        return await self._call({"mode": "decrypt", "req": req, "data": _b64(blob), "config": config})

    async def _call(self, request: dict[str, Any]) -> bytes:
        await self.start()
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise ProbeError("bundle worker pipes unavailable")
        try:
            proc.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
            await proc.stdin.drain()
            raw = await asyncio.wait_for(self._readline(proc.stdout), timeout=self.timeout)
        except TimeoutError as exc:
            await self.aclose()
            raise ProbeError(f"bundle worker timed out after {self.timeout}s ({request.get('mode')})") from exc
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            await self.aclose()
            raise ProbeError(f"bundle worker died: {exc}") from exc
        if raw is None:
            stderr = await self._stderr_tail(proc)
            await self.aclose()
            raise ProbeError(f"bundle worker returned nothing: {stderr}")
        try:
            result = json.loads(raw)
        except ValueError as exc:
            raise ProbeError(f"bundle worker returned non-JSON: {raw[:200]!r}") from exc
        if result.get("status") != "ok":
            raise ProbeError(f"bundle failed ({request.get('mode')}): {str(result.get('error'))[:500]}")
        return base64.b64decode(result["data"], validate=True)

    async def _readline(self, stream: asyncio.StreamReader) -> str | None:
        while True:
            raw = await stream.readline()
            if not raw:
                return None
            text = raw.decode("utf-8", errors="replace").strip()
            if text.startswith("{"):
                return text
            if text:
                self.stray.append(text[:120])

    async def _stderr_tail(self, proc: asyncio.subprocess.Process) -> str:
        if proc.stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(proc.stderr.read(), timeout=2)
        except TimeoutError:
            return ""
        text = data.decode("utf-8", errors="replace").strip()
        if "Cannot find module" in text:
            text += " | install jsdom+canvas: cd src/captcha/adapters/hcaptcha_js && npm ci"
        return text[-800:]


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
    stray_before: int = 0

    @property
    def total_ms(self) -> float:
        return self.cfg_ms + self.n_ms + self.get_ms


@dataclass(slots=True)
class CheckConfig:
    """checksiteconfig answer: a challenge to stamp, or hCaptcha's passive mode."""

    challenge: dict[str, Any] | None
    features: dict[str, Any]
    passive: bool


async def checksite_config(client: Any, params: wire.Params) -> CheckConfig:
    """The wire.checksite_config call, plus the passive branch the solver lacks.

    A key in passive mode answers {"pass": true} with no "c"; the widget then
    asks getcaptcha for a token without any n-token at all. wire.checksite_config
    treats that answer as unsolvable, so the probe does the call itself to be
    able to measure both modes.
    """
    query: dict[str, Any] = {
        "v": params.version,
        "host": params.host,
        "sitekey": params.sitekey,
        "sc": "1",
        "swa": "1",
        "spst": "1",
    }
    if params.rqdata:
        query["rqdata"] = params.rqdata
    resp = await client.post(
        wire.CHECK_SITE_CONFIG,
        params=query,
        data=b"",
        timeout=wire.HTTP_TIMEOUT,
        headers={
            "User-Agent": params.user_agent,
            "Accept": "application/json",
            "Content-Type": "text/plain",
            "Referer": wire.REFERER,
        },
    )
    if resp.status != 200:
        raise ProbeError(f"checksiteconfig HTTP {resp.status}")
    try:
        info = resp.json()
    except ValueError as exc:
        raise ProbeError("checksiteconfig returned non-JSON") from exc
    if not isinstance(info, dict):
        raise ProbeError("checksiteconfig returned no object")
    features = info.get("features") if isinstance(info.get("features"), dict) else {}
    challenge = info.get("c")
    if isinstance(challenge, dict):
        return CheckConfig(challenge=challenge, features=features, passive=False)
    if info.get("pass") is True:
        return CheckConfig(challenge=None, features=features, passive=True)
    raise ProbeError(f"checksiteconfig: no challenge (keys={sorted(info)})")


async def get_captcha_passive(client: Any, params: wire.Params) -> dict[str, Any]:
    """getcaptcha for a passive key: plain form, empty n-token, no challenge blob."""
    body = {key: value for key, value in wire.body_payload(params, "").items() if value is not None}
    body["pst"] = "false"
    resp = await client.post(
        f"{wire.GET_CAPTCHA}/{params.sitekey}",
        data=body,
        timeout=wire.HTTP_TIMEOUT,
        headers={"User-Agent": params.user_agent, "Accept": "application/json", "Referer": wire.REFERER},
    )
    if resp.status != 200:
        raise ProbeError(f"getcaptcha HTTP {resp.status}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProbeError("getcaptcha returned non-JSON") from exc
    return data if isinstance(data, dict) else {}


def _pass_token(data: dict[str, Any]) -> str | None:
    """wire.pass_token plus the test key's non-P1_ pass UUID."""
    token = wire.pass_token(data)
    if token is None and data.get("pass") is True:
        candidate = data.get("generated_pass_UUID")
        if isinstance(candidate, str) and candidate:
            token = candidate
    return token


async def measure(site: Site, index: int, worker: BundleWorker, args: argparse.Namespace) -> Run:
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
    stray_before = len(worker.stray)
    client = Client(impersonate=args.impersonate, timeout=args.timeout)
    try:
        async with client:
            started = time.monotonic()
            config = await checksite_config(client, params)
            run.features = sorted(config.features)
            run.encrypted_request = bool(config.features.get("enc_get_req"))
            run.cfg_ms = _ms(started)

            if config.passive:
                run.challenge_type = "passive"
                started = time.monotonic()
                data = await get_captcha_passive(client, params)
                run.get_ms = _ms(started)
            else:
                challenge = config.challenge or {}
                kind = challenge.get("type")
                run.challenge_type = kind if isinstance(kind, str) else None
                req = challenge.get("req")
                if not isinstance(req, str) or not req:
                    raise ProbeError("checksiteconfig returned no challenge JWT")

                started = time.monotonic()
                n_token = await worker.solve(
                    req=req, host=site.host, href=site.page, config=params.bundle_config(), vm_data=params.vm_data
                )
                run.n_len = len(n_token)
                run.n_ms = _ms(started)
                if not n_token:
                    raise ProbeError("bundle produced an empty n-token")

                started = time.monotonic()
                data = await wire.get_captcha(client, params, challenge, config.features, n_token, req, worker)
                run.get_ms = _ms(started)

        token = _pass_token(data)
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
    finally:
        run.stray_before = len(worker.stray) - stray_before
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
        "stray_lines": sum(run.stray_before for run in runs),
        "cfg_ms_p50": _pct([run.cfg_ms for run in runs], 50),
        "n_ms_p50": _pct([run.n_ms for run in runs], 50),
        "get_ms_p50": _pct([run.get_ms for run in runs], 50),
        "total_ms_p50": _pct([run.total_ms for run in runs], 50),
        "total_ms_p95": _pct([run.total_ms for run in runs], 95),
        "total_ms_mean": round(statistics.fmean([run.total_ms for run in runs]), 1) if runs else 0.0,
    }


async def probe(sites: list[Site], args: argparse.Namespace) -> dict[str, Any]:
    worker = BundleWorker(
        node_binary=args.node, timeout=args.bundle_timeout, node_path=[str(p) for p in args.node_path]
    )
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "client_version": args.client_version,
        "user_agent": args.user_agent,
        "impersonate": args.impersonate,
        "node": worker.node_binary,
        "node_path": worker.node_path,
        "sites": [],
    }
    try:
        await worker.start()
        for site in sites:
            runs: list[Run] = []
            for index in range(1, args.runs + 1):
                run = await measure(site, index, worker, args)
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
    finally:
        report["stray_lines"] = list(worker.stray[:20])
        await worker.aclose()
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


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


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
    if report.get("stray_lines"):
        print(f"\nstray stdout lines discarded by the probe: {len(report['stray_lines'])} (adapter console leak)")


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
