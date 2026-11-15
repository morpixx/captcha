"""Akamai payload builder: classic sensor_data, SBSD JSON, and the JS seam.

Two wire shapes, one signal source:

- classic Bot Manager: POST {"sensor_data": "<opaque>"} (form on older tenants)
- SBSD (Bot Manager Premier): a JSON envelope posted to /sec/cp_challenge/sbsd

The opaque string is produced by the tenant's own bmak script. This builder does
not re-implement that script: it resolves the script from the task, evals it in
the worker's shared V8 runtime behind a stubbed browser surface
(engines/akamai/harness.js), and takes the exact body the script tried to POST.
A script that cannot run raises SensorError -- there is no fallback that mints a
plausible-looking blob.

The sibling module captcha.engines.akamai.sensor keeps the section model and a
labelled model encoder for the two documented v3 stages. It is reachable only
with sensor_encoder="model" in BuildContext.extra, is never a silent fallback,
and its output is still graded solely by the _abck state tail (see the solver),
so a wrong encoding can never be reported as a success.
"""

from __future__ import annotations

import functools
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ConfigError, JSRuntimeError, SensorError, UnsolvableError
from captcha.engines.base import BuildContext, SensorBuilder
from captcha.engines.registry import register_builder

from . import sensor

#: Where the bmak harness lives. Read once per process; it is an asset, not state.
HARNESS = Path(__file__).with_name("harness.js")

#: A sensor script is one synchronous pass; anything slower is a hang.
JS_TIMEOUT_MS = 8000

#: Encoder names accepted in BuildContext.extra["sensor_encoder"].
ENCODERS = ("js", "model")

#: Max characters of a JS error message carried into an error context.
MAX_ERROR_CHARS = 160


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
    """Run a tenant script in the runtime and return what it tried to send.

    Returns {"sent": [{"url", "method", "body", "headers"}], "errors": [str]}.
    The caller decides what a usable payload looks like; bodies never leave the
    worker: contract section 5 forbids payloads in logs and error contexts.
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
    return _parsed(out, engine="akamai")


def _parsed(out: Any, *, engine: str) -> dict[str, Any]:
    """Decode the harness reply. The harness returns JSON so every runtime agrees.

    py-mini-racer hands back a JS object proxy, the sidecar hands back text; a JSON
    string is the one shape both can produce without a marshalling layer.
    """
    if not isinstance(out, str):
        raise JSRuntimeError(
            f"{engine} harness returned no capture",
            context={"type": type(out).__name__},
        )
    try:
        parsed = json.loads(out)
    except ValueError as exc:
        raise JSRuntimeError(
            f"{engine} harness reply is not JSON",
            context={"chars": len(out)},
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("sent"), list):
        raise JSRuntimeError(
            f"{engine} harness returned no capture list",
            context={"keys": sorted(parsed) if isinstance(parsed, dict) else []},
        )
    return parsed


def target_from(captured: dict[str, Any]) -> str | None:
    """The endpoint the script itself posted to: the tenant's own sensor URL.

    Akamai serves the sensor endpoint from a per-customer path that is only
    discoverable from the live script, so when the client sends the script inline
    this is the one trustworthy target. The solver uses it ahead of the page URL
    and never rewrites it onto another host.
    """
    for item in captured.get("sent") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def sensor_data_from(captured: dict[str, Any]) -> str:
    """Pull the sensor_data value out of the script's own POST."""
    sent = captured.get("sent") or []
    for item in sent:
        if not isinstance(item, dict):
            continue
        data = _sensor_data(item.get("body"))
        if data:
            return data

    errors = [str(err)[:MAX_ERROR_CHARS] for err in (captured.get("errors") or [])]
    raise SensorError(
        "akamai: the sensor script produced no sensor_data POST",
        context={"requests": len(sent), "script_errors": errors[:3]},
    )


def _sensor_data(body: Any) -> str | None:
    """Accept the JSON body (v3 tenants) and the form body (2.x). Nothing else."""
    if not isinstance(body, str) or not body.strip():
        return None
    text = body.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            value = parsed.get("sensor_data")
            if isinstance(value, str) and value.strip():
                return value.strip()
    if "sensor_data=" in text:
        for key, value in parse_qsl(text, keep_blank_values=True):
            if key == "sensor_data" and value.strip():
                return value.strip()
    return None


@register_builder(AntibotEngine.AKAMAI)
class AkamaiSensorBuilder(SensorBuilder):
    """Classic sensor_data string plus the SBSD JSON envelope."""

    name = "akamai"
    #: Only the JS encoder needs the script; the model encoder works without it.
    requires_challenge_js = False
    uses_js_runtime = True

    #: Sensor format generation written as the first serialized field.
    VERSION: str = sensor.VERSION

    async def build(self, ctx: BuildContext) -> str:
        """Produce the sensor_data value for the classic POST."""
        _require_inputs(ctx)
        mode = str(ctx.extra.get("sensor_encoder") or "js").lower()
        if mode == "js":
            return await self._from_script(ctx)
        if mode == "model":
            return self._from_sections(ctx)
        raise ConfigError(
            f"akamai: unknown sensor_encoder {mode!r}",
            context={"allowed": list(ENCODERS), "site": ctx.site_url},
        )

    def build_sbsd(self, ctx: BuildContext) -> dict[str, Any]:
        """Produce the SBSD JSON envelope.

        SBSD's own data-generation step has no public schema. Two honest inputs
        exist: the client's own body (payload mode -- it ran the script on the
        page) or the section model below. Both are graded by the cookie state
        afterwards, never accepted on a 200 alone.
        """
        _require_inputs(ctx)
        supplied = ctx.extra.get("sbsd_body")
        if isinstance(supplied, dict) and supplied:
            return dict(supplied)

        rows = sensor.sections(ctx)
        if not rows:
            raise SensorError(
                "akamai_sbsd: no signals to serialize",
                context={"site": ctx.site_url, "extra_keys": sorted(ctx.extra)},
            )
        return {
            "version": str(ctx.extra.get("sbsd_version") or self.VERSION),
            "sensor_data": ":".join(f"{name}={body}" for name, body in rows),
            "ts": int(ctx.timestamp_ms or time.time() * 1000),
        }

    # --- encoders -------------------------------------------------------

    async def _from_script(self, ctx: BuildContext) -> str:
        script = _script_text(ctx)
        runtime = ctx.extra.get("js_runtime")
        if runtime is None:
            raise ConfigError(
                "akamai: the sensor script needs the shared JS runtime, but SolverContext.js_runtime is None",
                context={"site": ctx.site_url},
            )
        captured = await capture_requests(runtime, script, page_url=ctx.site_url or "")
        target = target_from(captured)
        if target:
            # The solver reads this back off the BuildContext it just passed in.
            ctx.extra["sensor_target"] = target
        return sensor_data_from(captured)

    def _from_sections(self, ctx: BuildContext) -> str:
        rows = sensor.sections(ctx)
        if not rows:
            raise SensorError(
                "akamai: no signals to serialize",
                context={"site": ctx.site_url, "extra_keys": sorted(ctx.extra)},
            )
        return sensor.encode_sections(
            rows,
            script=ctx.challenge_js,
            bm_sz=ctx.cookies.get("bm_sz"),
            version=self.VERSION,
        )


def _require_inputs(ctx: BuildContext) -> None:
    """A fingerprint is mandatory; behaviour is not -- a bot has no mouse path.

    An empty behaviour pack produces a shorter payload, which is the honest
    representation of a session that never touched the page.
    """
    if ctx.fingerprint is None:
        raise SensorError(
            "akamai builder needs a fingerprint",
            context={"site": ctx.site_url},
        )


def _script_text(ctx: BuildContext) -> str:
    script = ctx.challenge_js or ctx.extra.get("sensor_js")
    if not isinstance(script, str) or not script.strip():
        raise UnsolvableError(
            "akamai: sensor_data is produced by the tenant's bmak script; send sensor_js "
            "(the script source) or sensor_js_url so the solver can fetch it",
            context={"site": ctx.site_url},
        )
    return script
