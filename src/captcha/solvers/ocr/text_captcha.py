"""text_captcha - answer a natural-language riddle via an LLM endpoint.

Speaks the OpenAI /v1/chat/completions wire format, so it works against any
compatible server: Ollama, vLLM, llama.cpp server, OpenAI itself.

Transport and endpoint policy live in _llm.py; this file resolves settings, builds
the request and normalises the answer. The worker's ctx.http is used when it
ducks like a client, otherwise a short-lived httpx client is opened - the text
type rides the cv lane and that worker has no shared HTTP client.
"""

from __future__ import annotations

import logging
import time
from typing import Any, ClassVar

import httpx

from captcha.config import get_settings
from captcha.domain import CaptchaType, SolverResult, Task
from captcha.domain.enums import SolveMode
from captcha.domain.errors import ModelInferenceError, UnsolvableError
from captcha.solvers.base import Solver, SolverContext, SolverInfo
from captcha.solvers.ocr._llm import (
    REQUEST_TIMEOUT_SEC,
    SYS_PROMPT,
    chat,
    resolve_endpoint,
    resolve_model,
    strip_answer,
    task_deadline,
)

log = logging.getLogger(__name__)

_MAX_QUESTION_CHARS = 8_000
_MAX_ANSWER_TOKENS = 32


class TextCaptchaSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="text_captcha",
        types=(CaptchaType.TEXT,),
        http=True,
        version="0.2.0",
    )

    def __init__(self) -> None:
        super().__init__()
        self._settings: Any = None

    async def prepare(self, ctx: SolverContext) -> None:
        self._settings = getattr(ctx, "config", None) or get_settings()

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.perf_counter()
        settings = self._settings or getattr(ctx, "config", None) or get_settings()

        payload = task.payload or {}
        question = payload.get("comment") or payload.get("question") or payload.get("text")
        if not isinstance(question, str) or not question.strip():
            raise UnsolvableError("text_captcha: payload missing 'comment' (the question text)")
        question = question.strip()
        if len(question) > _MAX_QUESTION_CHARS:
            raise UnsolvableError(
                "text_captcha: question is too long",
                context={"length": len(question), "cap": _MAX_QUESTION_CHARS},
            )

        url = resolve_endpoint(settings, payload)
        model = resolve_model(settings, payload)
        if "api_key" in payload:
            log.warning("text_captcha.payload_api_key_ignored")
        api_key = str(getattr(settings, "llm_api_key", "") or "").strip()

        headers: dict[str, str] = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYS_PROMPT},
                {"role": "user", "content": question},
            ],
            "temperature": 0,
            "max_tokens": _MAX_ANSWER_TOKENS,
            "stream": False,
        }

        deadline = task_deadline(task.deadline_ts)
        shared = getattr(ctx, "http", None)
        client = shared if callable(getattr(shared, "post", None)) else httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC)
        try:
            raw = await chat(client, url, body, headers, deadline)
        finally:
            if client is not shared:
                await client.aclose()

        answer = strip_answer(raw)
        if not answer:
            raise ModelInferenceError("text_captcha: LLM returned an empty answer")

        return SolverResult(
            text=answer,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            extra={"model": model},
        )
