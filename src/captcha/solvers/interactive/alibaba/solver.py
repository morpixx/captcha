"""Alibaba / Aliyun NoCaptcha — slider with UMID fingerprint.

Flow (observed; schema shifts per site):
  1. POST  /service/um.json  (ynuf.aliapp.org)              -> `tn` (UMID token)
     Payload is a per-visit AES-GCM blob of browser signals.
  2. GET   /nocaptcha/get.jsonp?... (bg puzzle metadata)
  3. GET   <bg>, <puzzle>
  4. Solve slider CV.
  5. GET   /nocaptcha/analyze.jsonp?csessionid=<sess>&sig=<sig>&...  -> token

The site then submits { nc_sid, nc_token, scene } back to its own backend.
"""

from __future__ import annotations

import time
from typing import Any

from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.errors import CaptchaError, UnsolvableError
from captcha.solvers.registry import register_solver
from captcha.solvers.base import SolverContext, SolverInfo

from .._base import InteractiveBase
from . import endpoints as ep


@register_solver
class AlibabaSolver(InteractiveBase):
    info = SolverInfo(
        name="alibaba",
        types=(CaptchaType.ALIBABA,),
        http=True,
        browser=False,
        needs_js_runtime=True,  # UMID collect is heavy JS
        experimental=True,
    )

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        self._require(task.payload, "appkey", "scene")
        if ctx.http is None:
            raise UnsolvableError("alibaba solver requires ctx.http", context={})

        _ = (task.payload["appkey"], task.payload["scene"])  # intentional unused: used after RE

        # Live-RE items pending capture:
        # - UMID collect payload (see AliSec UM SDK 3.x `_init._gather`)
        # - nc_token signature (HMAC-MD5 over sess + nonce + `wua`)
        # - bg/piece URL lifetime (vendor expires in ~60s)
        raise CaptchaError(
            "alibaba nocaptcha solve not yet implemented — see runbooks/alibaba.md",
            context={
                "needs": [
                    "umid_collect_payload",
                    "nc_token_signature",
                    "jsonp_envelope_parsing",
                ],
                "endpoints": {
                    "umid": ep.UMID_ENDPOINT,
                    "get": ep.NC_GET,
                    "analyze": ep.NC_ANALYZE,
                },
                "started": time.time(),
            },
        )
