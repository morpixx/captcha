"""Async client for the hCaptcha hsw/hsj bundle worker (server.js).

The bundle is obfuscated JS that probes a browser-shaped realm (jsdom + canvas);
mini-racer and the plain node:vm sidecar do not carry the DOM surface it reads,
so it gets its own persistent Node process. Protocol: one JSON line in, one JSON
line out, bytes base64-encoded:

    -> {"mode": "solve", "req": "<jwt>", "host": "example.com", "href": "...", "config": {...}}
    <- {"status": "ok", "kind": "string", "data": "<base64>", "len": 0}
    <- {"status": "error", "error": "<message>"}

One runner per worker, shared across tasks: realms and the on-disk bundle cache
are warmed per client build, and restarting Node per task throws that away.
node_modules is not vendored - run "npm ci" in this directory once per image.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import shutil
from collections import deque
from pathlib import Path
from typing import Any

from captcha.domain.errors import ConfigError, JSRuntimeError

log = logging.getLogger(__name__)

_SERVER = Path(__file__).with_name("server.js")
_NODE_CANDIDATES = (
    r"C:\Program Files\nodejs\node.exe",
    r"C:\Program Files (x86)\nodejs\node.exe",
)


class JsBundleRunner:
    """Owns one Node process running adapters/hcaptcha_js/server.js."""

    def __init__(
        self,
        *,
        node_binary: str = "node",
        server_path: str | Path | None = None,
        timeout: float = 120.0,
        cwd: str | Path | None = None,
    ) -> None:
        self.node_binary = _resolve_node(node_binary)
        self.server_path = Path(server_path or _SERVER)
        if not self.server_path.is_file():
            raise ConfigError(f"hcaptcha js: worker not found: {self.server_path}")
        self.timeout = timeout
        self.cwd = Path(cwd) if cwd else self.server_path.parent
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr: deque[str] = deque(maxlen=40)
        self._drainer: asyncio.Task[None] | None = None

    # --- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            return
        env = dict(os.environ)
        env.setdefault("NODE_ENV", "production")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self.node_binary,
                str(self.server_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cwd),
                env=env,
            )
        except OSError as exc:
            raise ConfigError(
                "hcaptcha js: cannot start the bundle worker",
                context={"node": self.node_binary, "error": str(exc)},
            ) from exc
        self._drainer = asyncio.create_task(self._drain_stderr(self._proc))
        log.info("hcaptcha_js.started pid=%s node=%s", self._proc.pid, self.node_binary)

    async def aclose(self) -> None:
        proc, self._proc = self._proc, None
        drainer, self._drainer = self._drainer, None
        if proc is not None and proc.returncode is None:
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
        if drainer is not None:
            drainer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drainer

    async def __aenter__(self) -> JsBundleRunner:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # --- operations -------------------------------------------------------

    async def solve(
        self,
        *,
        req: str,
        host: str,
        href: str,
        config: dict[str, Any],
        vm_data: str | None = None,
    ) -> str:
        request: dict[str, Any] = {
            "mode": "solve",
            "req": req,
            "host": host,
            "href": href,
            "config": config,
        }
        if vm_data is not None:
            request["vm_data"] = vm_data
        result = await self._call(request)
        return _decode(result, "solve").decode("latin1")

    async def encrypt(self, req: str, plaintext: bytes, config: dict[str, Any]) -> bytes:
        result = await self._call(
            {"mode": "encrypt", "req": req, "data": base64.b64encode(plaintext).decode("ascii"), "config": config}
        )
        return _decode(result, "encrypt")

    async def decrypt(self, req: str, blob: bytes, config: dict[str, Any]) -> bytes:
        result = await self._call(
            {"mode": "decrypt", "req": req, "data": base64.b64encode(blob).decode("ascii"), "config": config}
        )
        return _decode(result, "decrypt")

    # --- internals --------------------------------------------------------

    async def _call(self, request: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if self._proc is None or self._proc.returncode is not None:
                await self.start()
            proc = self._proc
            if proc is None or proc.stdin is None or proc.stdout is None:
                raise JSRuntimeError("hcaptcha js: bundle worker pipes unavailable")

            line = json.dumps(request, separators=(",", ":")) + "\n"
            try:
                proc.stdin.write(line.encode("utf-8"))
                await proc.stdin.drain()
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=self.timeout)
            except TimeoutError as exc:
                await self.aclose()
                raise JSRuntimeError(
                    "hcaptcha js: bundle worker timed out",
                    context={"mode": request.get("mode"), "timeout_s": self.timeout},
                ) from exc
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                await self.aclose()
                raise JSRuntimeError(
                    "hcaptcha js: bundle worker died",
                    context={"mode": request.get("mode"), "stderr": self._stderr_tail()},
                ) from exc
            if not raw:
                await self.aclose()
                raise JSRuntimeError(
                    "hcaptcha js: bundle worker returned nothing",
                    context={"mode": request.get("mode"), "stderr": self._stderr_tail()},
                )
            try:
                result = json.loads(raw)
            except ValueError as exc:
                raise JSRuntimeError(
                    "hcaptcha js: bundle worker returned non-JSON",
                    context={"line": raw[:200].decode("utf-8", errors="replace")},
                ) from exc
            if result.get("status") != "ok":
                raise JSRuntimeError(
                    "hcaptcha js: bundle failed",
                    context={"mode": request.get("mode"), "error": str(result.get("error"))[:600]},
                )
            return result

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        stream = proc.stderr
        if stream is None:
            return
        while True:
            chunk = await stream.readline()
            if not chunk:
                return
            text = chunk.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr.append(text)

    def _stderr_tail(self) -> list[str]:
        return list(self._stderr)


def _resolve_node(node_binary: str) -> str:
    resolved = shutil.which(node_binary)
    if resolved:
        return resolved
    for candidate in _NODE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise ConfigError(
        "hcaptcha js: Node.js not found",
        context={"node_binary": node_binary, "hint": "install Node 20+ or pass node_binary"},
    )


def _decode(result: dict[str, Any], mode: str) -> bytes:
    data = result.get("data")
    if not isinstance(data, str):
        raise JSRuntimeError(f"hcaptcha js: {mode} returned no data", context={"result": sorted(result)})
    try:
        return base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise JSRuntimeError(f"hcaptcha js: {mode} returned bad base64", context={}) from exc
