"""Google BotGuard solver.

BotGuard is the bytecode VM behind reCAPTCHA, YouTube/WAA and Google
account signals. The product hands out two halves -- an interpreter script
and a program blob -- and the VM returns an opaque response token that only
Google can evaluate.

Flow: parse the program (bare f.txt or a JSON challenge wrapper), fetch the
interpreter when the client only sent its URL, run interpreter + snapshot
call in ctx.js_runtime, return the token. No verify call: which endpoint
consumes the token is the caller's business and differs per product.

Errors: missing program/interpreter -> UnsolvableError, VM refused to
initialise -> SensorError, empty answer -> ChallengeExpiredError (f.txt
rotates and a stale program is the normal retry), missing runtime ->
ConfigError.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError, UnsolvableError
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase

from . import payload as fields
from . import wire


class BotGuardSolver(AntibotBase):
    """Google BotGuard VM."""

    engine = AntibotEngine.BOTGUARD

    info: ClassVar[SolverInfo] = SolverInfo(
        name="botguard",
        types=(CaptchaType.BOTGUARD,),
        http=True,
        needs_js_runtime=True,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        payload = self.payload_or_fail(task)
        self.require_ctx(ctx, "http", "sensors", "js_runtime")

        raw_program = fields.program_raw(payload)
        if not raw_program:
            raise UnsolvableError(
                "botguard requires the scraped f.txt program (payload ftx/program)",
                context={"task_id": str(task.id)},
            )
        program = wire.parse_program(
            raw_program,
            global_name=fields.global_name(payload),
            interpreter_url=fields.interpreter_url(payload),
        )
        interpreter = fields.interpreter_js(payload)
        if not interpreter and program.interpreter_url:
            interpreter = await self._fetch_interpreter(ctx, program.interpreter_url)
        if not interpreter:
            raise UnsolvableError(
                "botguard requires the interpreter script or its interpreter_url",
                context={"task_id": str(task.id), "product": fields.product(payload)},
            )

        product = fields.product(payload)
        builder, build_ctx = self.build_sensor(
            ctx,
            payload,
            timestamp_ms=fields.timestamp_ms(payload),
            cookies=fields.cookies(payload),
            headers=fields.headers(payload),
            extra={
                "program": program.program,
                "global_name": program.global_name,
                "interpreter_js": interpreter,
                "product": product,
                "ua": fields.user_agent(task, payload),
            },
        )
        plan = await builder.build(build_ctx)
        raw = await ctx.js_runtime.exec_script(
            plan["script"],
            plan.get("globals") or {},
            timeout_ms=fields.js_timeout_ms(task, payload),
        )
        token = wire.token_from_runtime(raw)
        extra: dict[str, Any] = {"product": product}
        user_agent = fields.user_agent(task, payload)
        if user_agent:
            extra["userAgent"] = user_agent
        return SolverResult(
            token=token,
            extra=extra,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def _fetch_interpreter(self, ctx: SolverContext, url: str) -> str:
        response = await ctx.http.get(url)
        status = int(getattr(response, "status", 0) or 0)
        if status and not 200 <= status < 300:
            raise SensorError(
                "botguard interpreter fetch failed",
                context={"status": status, "url": url},
            )
        body = getattr(response, "body", b"")
        if isinstance(body, (bytes, bytearray)):
            return bytes(body).decode("utf-8", "replace")
        text = getattr(response, "text", "")
        return text if isinstance(text, str) else str(body or "")
