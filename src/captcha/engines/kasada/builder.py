"""Kasada payload builder: run p.js in the JS runtime, take the /tl body.

Kasada does not hand out a token: the SDK script (p.js / ips.js) hosts a custom
VM that probes the environment and POSTs a fingerprint blob to
<prefix>/tl. The server answers 200 {"reload": true} plus the x-kpsdk-ct /
x-kpsdk-st headers. Reproducing that blob means running the VM -- the bytecode is
the protocol (docs/reverse-2026.md section 6) -- so this builder evals the
script in the worker's shared V8 runtime behind a stubbed browser surface
(engines/kasada/harness.js) and returns the exact bytes and headers the script
tried to send. No script or no runtime is a typed error, never a plausible blob.

Headers for /tl come from the script's own setRequestHeader calls; the solver
adds the browser-identity headers (UA, Origin, Referer) and content-type.

The cd (per-request PoW) envelope is assembled here too, from a VM capture or
payload extras -- see captcha.solvers.antibot.kasada.pow, which refuses to
invent answers.
"""

from __future__ import annotations

import base64
import functools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ConfigError, JSRuntimeError, SensorError, UnsolvableError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder

from . import pow

#: The stubbed-browser harness. Read once per process.
HARNESS = Path(__file__).with_name("harness.js")

#: One VM pass; anything slower is a hang, not a slow site.
JS_TIMEOUT_MS = 12000

#: Path fragment that identifies the fingerprint POST.
TL_MARKER = "/tl"

#: Max characters of a JS error carried into an error context.
MAX_ERROR_CHARS = 160


@dataclass(frozen=True, slots=True)
class TlRequest:
    """What the VM wanted to POST to /tl."""

    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    url: str = ""

    @property
    def payload_b64(self) -> str:
        return base64.b64encode(self.body).decode()


@functools.cache
def harness_source() -> str:
    return HARNESS.read_text(encoding="utf-8")


async def capture_requests(
    runtime: Any,
    script: str,
    *,
    page_url: str = "",
    timeout_ms: int = JS_TIMEOUT_MS,
) -> dict[str, Any]:
    """Run the SDK script and return {"sent": [...], "errors": [...]}.

    Bodies arrive base64 encoded. They never leave the worker: contract section 5
    forbids payloads in logs and error contexts, so callers carry counts only.
    """
    code = "\n".join(
        (
            harness_source(),
            "__install(__page_url);",
            "__run(__challenge_source);",
            "return __finish();",
        )
    )
    out = await runtime.exec_script(
        code,
        {"__challenge_source": script, "__page_url": page_url},
        timeout_ms=timeout_ms,
    )
    return _parsed(out)


def _parsed(out: Any) -> dict[str, Any]:
    """Decode the harness reply; see the Akamai builder for why it is JSON."""
    if not isinstance(out, str):
        raise JSRuntimeError(
            "kasada harness returned no capture",
            context={"type": type(out).__name__},
        )
    try:
        parsed = json.loads(out)
    except ValueError as exc:
        raise JSRuntimeError("kasada harness reply is not JSON", context={"chars": len(out)}) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("sent"), list):
        raise JSRuntimeError(
            "kasada harness returned no capture list",
            context={"keys": sorted(parsed) if isinstance(parsed, dict) else []},
        )
    return parsed


def tl_request(captured: dict[str, Any]) -> TlRequest:
    """Pick the /tl POST out of the capture. Anything else is a script failure."""
    sent = [item for item in (captured.get("sent") or []) if isinstance(item, dict)]
    for item in sent:
        if TL_MARKER not in str(item.get("url") or ""):
            continue
        body = _decode(item.get("body_b64"))
        if not body:
            raise SensorError(
                "kasada: the VM posted an empty /tl body",
                context={"requests": len(sent)},
            )
        headers = {
            str(key).lower(): str(value)
            for key, value in (item.get("headers") or {}).items()
            if value not in (None, "")
        }
        return TlRequest(body=body, headers=headers, url=str(item.get("url") or ""))

    errors = [str(err)[:MAX_ERROR_CHARS] for err in (captured.get("errors") or [])]
    raise SensorError(
        "kasada: the SDK script produced no /tl POST",
        context={"requests": len(sent), "script_errors": errors[:3]},
    )


def _decode(raw: Any) -> bytes:
    if not isinstance(raw, str) or not raw:
        return b""
    try:
        return base64.b64decode(raw, validate=False)
    except (ValueError, TypeError):
        return b""


@register_builder(AntibotEngine.KASADA)
class KasadaBuilder(SensorBuilder):
    """Kasada /tl payload plus the cd envelope."""

    name = "kasada"
    requires_challenge_js = True
    uses_js_runtime = True

    async def build(self, ctx: BuildContext) -> dict[str, Any]:
        """Return {payload_b64, headers, target, bytes, version}.

        The solver decodes payload_b64 and POSTs it to target with headers.
        """
        script = _script_text(ctx)
        runtime = ctx.extra.get("js_runtime")
        if runtime is None:
            raise ConfigError(
                "kasada: p.js must run in the shared JS runtime, but SolverContext.js_runtime is None",
                context={"site": ctx.site_url},
            )
        captured = await capture_requests(runtime, script, page_url=ctx.site_url or "")
        request = tl_request(captured)
        return {
            "payload_b64": request.payload_b64,
            "headers": dict(request.headers),
            "target": request.url,
            "bytes": len(request.body),
            "version": str(ctx.extra.get("kpsdk_v") or ""),
        }

    def build_cd(self, ctx: BuildContext) -> str:
        """Encoded x-kpsdk-cd header from payload-mode values.

        Raises SensorError when the answers are absent: cd is single use and its
        seed never reaches the wire, so there is nothing to compute locally.
        """
        return pow.encode(pow.require_envelope(ctx.extra))


def _script_text(ctx: BuildContext) -> str:
    script = ctx.challenge_js or ctx.extra.get("script") or ctx.extra.get("ips_js")
    if not isinstance(script, str) or not script.strip():
        raise UnsolvableError(
            "kasada: the SDK script is required; send script (source), script_url, "
            "or a websiteURL whose 429 body names ips.js",
            context={"site": ctx.site_url},
        )
    return script
