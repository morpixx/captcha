"""text_captcha: mocked LLM endpoint, no live model and no network."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from captcha.domain import CaptchaType
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.domain.errors import TimeoutError as CaptchaTimeout
from captcha.domain.task import Task, new_task_id
from captcha.solvers.base import SolverContext
from captcha.solvers.ocr import text_captcha as module
from captcha.solvers.ocr.text_captcha import TextCaptchaSolver


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHTTP:
    """Duck-typed stand-in for both httpx.AsyncClient and the transport Client."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> Any:
        self.calls.append({"url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:
        self.closed = True


def _reply(content: str) -> FakeResponse:
    return FakeResponse(payload={"choices": [{"message": {"content": content}}]})


def _settings(endpoint: str = "http://127.0.0.1:11434", api_key: str = "") -> SimpleNamespace:
    return SimpleNamespace(llm_endpoint=endpoint, llm_model="llama3.1:8b", llm_api_key=api_key)


def _task(payload: dict, *, deadline_ts: float | None = None) -> Task:
    return Task(id=new_task_id(), type=CaptchaType.TEXT, payload=payload, deadline_ts=deadline_ts)


def _solve(payload: dict, http: FakeHTTP | None = None, settings: Any = None, deadline_ts: float | None = None):
    ctx = SolverContext(http=http, config=settings or _settings())
    return asyncio.run(TextCaptchaSolver().solve_http(_task(payload, deadline_ts=deadline_ts), ctx))


def test_llm_answer_is_normalized():
    http = FakeHTTP(_reply("  Answer: Four.  "))
    result = _solve({"comment": "What is 2 + 2?"}, http=http)
    assert result.text == "Four"
    assert result.extra["model"] == "llama3.1:8b"


def test_multiline_answer_uses_first_line():
    http = FakeHTTP(_reply("seven\nbecause 3 + 4"))
    assert _solve({"comment": "3+4?"}, http=http).text == "seven"


def test_quoted_answer_is_unquoted():
    http = FakeHTTP(_reply('"hello"'))
    assert _solve({"comment": "word?"}, http=http).text == "hello"


def test_endpoint_is_normalized_to_chat_completions():
    http = FakeHTTP(_reply("x"))
    _solve({"comment": "q"}, http=http)
    assert http.calls[0]["url"] == "http://127.0.0.1:11434/v1/chat/completions"


def test_settings_api_key_becomes_bearer_header():
    http = FakeHTTP(_reply("x"))
    _solve({"comment": "q"}, http=http, settings=_settings(api_key="secret"))
    assert http.calls[0]["headers"]["authorization"] == "Bearer secret"


def test_payload_api_key_is_ignored():
    http = FakeHTTP(_reply("x"))
    _solve({"comment": "q", "api_key": "leak"}, http=http, settings=_settings(api_key="settings-key"))
    assert http.calls[0]["headers"]["authorization"] == "Bearer settings-key"


def test_ctx_http_is_used_and_not_closed():
    http = FakeHTTP(_reply("x"))
    _solve({"comment": "q"}, http=http)
    assert len(http.calls) == 1
    assert http.closed is False


def test_own_client_is_closed_when_worker_has_none():
    fake = FakeHTTP(_reply("x"))
    with patch.object(module.httpx, "AsyncClient", return_value=fake):
        result = _solve({"comment": "q"}, http=None)
    assert result.text == "x"
    assert fake.closed is True


def test_5xx_is_retried_once():
    http = FakeHTTP(FakeResponse(503, text="busy"), _reply("ok"))
    result = _solve({"comment": "q"}, http=http)
    assert result.text == "ok"
    assert len(http.calls) == 2


def test_5xx_twice_is_model_error():
    http = FakeHTTP(FakeResponse(503, text="busy"), FakeResponse(503, text="busy"))
    with pytest.raises(ModelInferenceError):
        _solve({"comment": "q"}, http=http)
    assert len(http.calls) == 2


def test_5xx_is_not_retried_when_budget_is_short():
    http = FakeHTTP(FakeResponse(503, text="busy"))
    with pytest.raises(ModelInferenceError):
        _solve({"comment": "q"}, http=http, deadline_ts=time.time() + 0.2)
    assert len(http.calls) == 1


def test_401_is_config_error_without_retry():
    http = FakeHTTP(FakeResponse(401, text="bad key"))
    with pytest.raises(ConfigError):
        _solve({"comment": "q"}, http=http)
    assert len(http.calls) == 1


def test_404_is_config_error_without_retry():
    http = FakeHTTP(FakeResponse(404, text="nope"))
    with pytest.raises(ConfigError):
        _solve({"comment": "q"}, http=http)
    assert len(http.calls) == 1


def test_429_is_model_error_without_immediate_retry():
    http = FakeHTTP(FakeResponse(429, text="slow down"))
    with pytest.raises(ModelInferenceError):
        _solve({"comment": "q"}, http=http)
    assert len(http.calls) == 1


def test_malformed_json_is_model_error():
    http = FakeHTTP(FakeResponse(200, payload=None))
    with pytest.raises(ModelInferenceError):
        _solve({"comment": "q"}, http=http)


def test_missing_choices_is_model_error():
    http = FakeHTTP(FakeResponse(200, payload={"nope": True}))
    with pytest.raises(ModelInferenceError):
        _solve({"comment": "q"}, http=http)


def test_empty_answer_is_model_error():
    http = FakeHTTP(_reply("   "))
    with pytest.raises(ModelInferenceError):
        _solve({"comment": "q"}, http=http)


def test_connection_error_is_retried_then_wrapped():
    http = FakeHTTP(ConnectionError("refused"), ConnectionError("refused"))
    with pytest.raises(ModelInferenceError):
        _solve({"comment": "q"}, http=http)
    assert len(http.calls) == 2


def test_timeout_is_a_timeout_error_without_retry():
    http = FakeHTTP(httpx.ReadTimeout("slow"))
    with pytest.raises(CaptchaTimeout):
        _solve({"comment": "q"}, http=http)
    assert len(http.calls) == 1


def test_missing_question_is_unsolvable():
    with pytest.raises(UnsolvableError):
        _solve({"comment": "   "}, http=FakeHTTP())


def test_overlong_question_is_unsolvable():
    with pytest.raises(UnsolvableError):
        _solve({"comment": "x" * 9000}, http=FakeHTTP())


def test_past_deadline_does_not_call_the_endpoint():
    http = FakeHTTP()
    with pytest.raises(CaptchaTimeout):
        _solve({"comment": "q"}, http=http, deadline_ts=time.time() - 1)
    assert http.calls == []


def test_missing_endpoint_is_config_error():
    with pytest.raises(ConfigError):
        _solve({"comment": "q"}, http=FakeHTTP(), settings=_settings(endpoint=""))


def test_endpoint_override_to_another_origin_is_refused():
    http = FakeHTTP()
    with pytest.raises(ConfigError):
        _solve({"comment": "q", "endpoint": "http://169.254.169.254:80"}, http=http)
    assert http.calls == []


def test_endpoint_override_on_the_same_origin_is_allowed():
    http = FakeHTTP(_reply("x"))
    _solve({"comment": "q", "endpoint": "http://127.0.0.1:11434"}, http=http)
    assert http.calls[0]["url"] == "http://127.0.0.1:11434/v1/chat/completions"


def test_metadata_endpoint_is_refused():
    with pytest.raises(ConfigError):
        _solve({"comment": "q"}, http=FakeHTTP(), settings=_settings(endpoint="http://169.254.169.254"))
