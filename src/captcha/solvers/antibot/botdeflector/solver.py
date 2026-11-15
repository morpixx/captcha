"""BotDeflector solver: the invisible flow over plain HTTP.

BotDeflector (botdeflector.eu, also Queue-it's softblock engine) hands the page
a flow token -- a JWT naming an ordered challengeFlow, typically inv, icon,
inv. This solver runs that flow in order:

    GET  {domain}/pow/get      x-tt-flow-token          -> {challengeToken}
    POST {domain}/pow/verify   solution + params + time -> step token
    GET  {domain}/verify-flow  flow + step tokens + id  -> ChallengeFlowResult

The proof of work is Cuckoo Cycle, computed in engines/botdeflector/pow.py from
the challenge JWT: every inv step of one flow shares one graph, so it is built
once per session. The visitor id that rides as solution_fingerprint and
x-tt-client-fingerprint comes from the engine builder. The final token is what
a 2captcha client spends: ?tt_solutions=<token> on the site, or Queue-it's own
/botdeflector/verify call.

No cookies come from BotDeflector itself (2026-10 capture); Queue-it's
Queue-it-visitorsession belongs to the host, so a jar is returned only when the
challenge host actually set one.

FILL: classic mode. A standalone widget gets its JWT from a /flow-token/ call
whose request shape is not public; a task that carries only siteKey/accountId
is refused with SensorError instead of guessing that call. FILL: icon steps
need a visual solver (their own /icon/get + /icon/verify + 2.5 s floor); this
HTTP lane refuses a flow that contains one with UnsolvableError.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar, NoReturn

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError, UnsolvableError
from captcha.engines.botdeflector import pow as pow_mod
from captcha.engines.botdeflector.builder import POW_GET_PATH, POW_VERIFY_PATH, VERIFY_FLOW_PATH
from captcha.solvers import SolverContext, SolverInfo
from captcha.solvers.antibot._base import AntibotBase

from . import payload as fields
from . import transport, wire


class BotDeflectorSolver(AntibotBase):
    """BotDeflector invisible challenge flow."""

    engine = AntibotEngine.BOTDEFLECTOR

    info: ClassVar[SolverInfo] = SolverInfo(
        name="botdeflector",
        types=(CaptchaType.BOTDEFLECTOR,),
        http=True,
        needs_js_runtime=False,
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        started = time.perf_counter()
        payload = self.payload_or_fail(task)
        self.require_ctx(ctx, "http", "sensors")

        user_agent = fields.user_agent(task, payload)
        done = fields.challenge_result(payload)
        if done:
            token = wire.flow_result_token({"ChallengeFlowResult": done})
            short_extra = {"userAgent": user_agent} if user_agent else None
            return SolverResult(token=token, extra=short_extra, latency_ms=(time.perf_counter() - started) * 1000.0)

        raw_flow = fields.flow_token(payload)
        if not raw_flow:
            self._no_flow_token(task, payload)
        flow = wire.decode_flow_token(raw_flow)
        unsupported = [kind for kind in flow.steps if kind not in wire.INVISIBLE_KINDS]
        if unsupported:
            raise UnsolvableError(
                "botdeflector flow needs a visual step this HTTP lane does not solve",
                context={"steps": list(flow.steps), "unsupported": unsupported},
            )
        if not flow.steps:
            raise UnsolvableError(
                "botdeflector flow token carries no challengeFlow",
                context={"claims": sorted(flow.claims)[:12]},
            )

        base = fields.domain_for(task, payload)
        page = fields.website_url(task, payload)
        builder, build_ctx = self.build_sensor(
            ctx,
            {**payload, "site_url": page or payload.get("site_url") or ""},
            cookies=payload.get("cookies"),
            headers=fields.headers(payload),
            extra={"fingerprint": fields.fingerprint(payload), "ua": user_agent},
        )
        plan = await builder.build(build_ctx)
        visitor = str(plan.get("visitor_id") or "")
        if not visitor:
            raise SensorError(
                "botdeflector builder returned no visitor id",
                context={"keys": sorted(plan)[:8]},
            )

        origin = transport.origin(page)
        same_site = transport.same_site(page, base)
        read_headers = transport.api_headers(
            flow.raw, origin=origin, referer=page, user_agent=user_agent, same_site=same_site
        )
        write_headers = transport.api_headers(
            "", origin=origin, referer=page, user_agent=user_agent, same_site=same_site
        )
        jar: dict[str, str] = {}
        graphs: dict[tuple[int, int, int, str], pow_mod.PackedGraph] = {}
        step_tokens: list[str] = []

        for _kind in flow.steps:
            challenge = await self._pow_get(ctx, base, read_headers, jar, flow)
            graph = graphs.get(challenge.graph_key)
            if graph is None:
                # Heavy configs are 2**20 edges; keep the worker loop free.
                graph = await asyncio.to_thread(pow_mod.build_graph, challenge.params)
                graphs[challenge.graph_key] = graph
            solve_started = time.perf_counter()
            solutions = await asyncio.to_thread(
                pow_mod.solve_challenges, list(challenge.challenges), challenge.params, graph
            )
            # FILL: whether /pow/verify enforces a minimum reported solve time. The
            # capture shows 150-420 ms of browser griding; we send what we spent.
            elapsed_s = time.perf_counter() - solve_started
            body = wire.pow_verify_body(
                challenge,
                solutions,
                visitor,
                elapsed_s=elapsed_s,
                flow_token=flow.raw,
            )
            response = await ctx.http.post(
                base + POW_VERIFY_PATH,
                json=body,
                headers=write_headers,
                cookies=jar or None,
            )
            jar.update(self.parse_cookies(transport.set_cookie(response)))
            transport.check_status(response, what="pow/verify")
            step_tokens.append(wire.step_token(response, what="pow/verify"))

        headers = transport.verify_flow_headers(
            flow.raw,
            step_tokens,
            visitor,
            origin=origin,
            referer=page,
            user_agent=user_agent,
            same_site=same_site,
        )
        response = await ctx.http.get(base + VERIFY_FLOW_PATH, headers=headers, cookies=jar or None)
        jar.update(self.parse_cookies(transport.set_cookie(response)))
        transport.check_status(response, what="verify-flow")
        token = wire.flow_result_token(response)

        solution_extra: dict[str, Any] = {}
        if user_agent:
            solution_extra["userAgent"] = user_agent
        if jar:
            solution_extra["cookies"] = dict(jar)
        return SolverResult(
            token=token,
            extra=solution_extra or None,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    async def _pow_get(
        self,
        ctx: SolverContext,
        base: str,
        headers: dict[str, str],
        jar: dict[str, str],
        flow: wire.FlowToken,
    ) -> wire.PowChallenge:
        response = await ctx.http.get(base + POW_GET_PATH, headers=headers, cookies=jar or None)
        jar.update(self.parse_cookies(transport.set_cookie(response)))
        transport.check_status(response, what="pow/get")
        return wire.pow_challenge(response, flow)

    def _no_flow_token(self, task: Task, payload: dict[str, Any]) -> NoReturn:
        """The flow token is the whole contract and the caller has to bring it."""
        if fields.site_key(payload) or fields.account_id(payload):
            raise SensorError(
                "botdeflector classic mode needs its flow token; the /flow-token/ request shape is not public",
                context={
                    "FILL": "capture the /flow-token/ request for siteKey + accountId, then mint here",
                    "has_site_key": bool(fields.site_key(payload)),
                    "has_account_id": bool(fields.account_id(payload)),
                },
            )
        raise UnsolvableError(
            "botdeflector requires the scraped botDeflectorJwtToken (flowToken)",
            context={"task_id": str(task.id)},
        )
