"""text_captcha — answer a natural-language riddle via LLM.

Uses the OpenAI /v1/chat/completions wire format. Works against any compatible server:
Ollama (`http://host:11434`), vLLM, llama.cpp server, OpenAI itself.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar

import httpx

from captcha_common import CaptchaType, SolverResult, Task
from captcha_common.config import Settings, get_settings
from captcha_common.enums import SolveMode
from captcha_common.errors import ConfigError, ModelInferenceError, TimeoutError as CaptchaTimeout, UnsolvableError
from captcha_solver_base.solver import Solver, SolverContext, SolverInfo

_SYS_PROMPT = (
    "You solve text CAPTCHA puzzles. Return ONLY the answer — no prose, no reasoning, "
    "no punctuation, no quotes, no leading label. If the answer is a number, return digits. "
    "If it's a word, return the single word in lowercase. Nothing else."
)


def _normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if endpoint.endswith("/v1"):
        base = endpoint
    elif endpoint.endswith("/v1/chat/completions"):
        return endpoint
    else:
        base = f"{endpoint}/v1"
    return f"{base}/chat/completions"


def _strip_answer(raw: str) -> str:
    # one-line, no surrounding quotes/punctuation noise
    text = raw.strip().splitlines()[0] if raw.strip() else ""
    return text.strip().strip('"').strip("'").strip(".").strip()


class TextCaptchaSolver(Solver):
    info: ClassVar[SolverInfo] = SolverInfo(
        name="text_captcha",
        types=(CaptchaType.TEXT,),
        http=True,
        browser=False,
    )

    def __init__(self) -> None:
        super().__init__()
        self._settings: Settings | None = None

    async def prepare(self, ctx: SolverContext) -> None:
        self._settings = ctx.config if isinstance(ctx.config, Settings) else get_settings()

    async def solve_http(self, task: Task, ctx: SolverContext) -> SolverResult:
        t0 = time.perf_counter()
        settings = self._settings or (ctx.config if isinstance(ctx.config, Settings) else get_settings())

        payload = task.payload or {}
        question = payload.get("comment") or payload.get("question") or payload.get("text")
        if not isinstance(question, str) or not question.strip():
            raise UnsolvableError("text_captcha: payload missing 'comment' (the question text)")

        endpoint = (payload.get("endpoint") or settings.llm_endpoint).strip()
        model = (payload.get("model") or settings.llm_model).strip()
        api_key = (payload.get("api_key") or settings.llm_api_key).strip()
        if not endpoint or not model:
            raise ConfigError("text_captcha: LLM endpoint/model not configured")

        url = _normalize_endpoint(endpoint)
        headers: dict[str, str] = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYS_PROMPT},
                {"role": "user", "content": question.strip()},
            ],
            "temperature": 0,
            "max_tokens": 32,
            "stream": False,
        }

        client: httpx.AsyncClient | None = getattr(ctx, "http", None) if ctx is not None else None
        owns_client = False
        if not isinstance(client, httpx.AsyncClient):
            client = httpx.AsyncClient(timeout=30.0)
            owns_client = True

        try:
            try:
                resp = await client.post(url, json=body, headers=headers)
            except httpx.TimeoutException as e:
                raise CaptchaTimeout(f"text_captcha: LLM endpoint timed out ({url})") from e
            except httpx.HTTPError as e:
                raise ModelInferenceError(
                    f"text_captcha: LLM endpoint network error: {e}",
                    context={"endpoint": url},
                ) from e

            if resp.status_code >= 400:
                raise ModelInferenceError(
                    f"text_captcha: LLM endpoint {resp.status_code}: {resp.text[:400]}",
                    context={"endpoint": url, "status": resp.status_code},
                )

            try:
                data = resp.json()
                raw = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError, TypeError) as e:
                raise ModelInferenceError(
                    f"text_captcha: malformed LLM response ({e})",
                    context={"endpoint": url},
                ) from e
        finally:
            if owns_client:
                await client.aclose()

        answer = _strip_answer(raw)
        if not answer:
            raise ModelInferenceError("text_captcha: LLM returned empty answer")

        return SolverResult(
            text=answer,
            solve_mode=SolveMode.HTTP,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            extra={"model": model},
        )
