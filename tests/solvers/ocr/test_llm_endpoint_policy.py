"""text_captcha LLM endpoint policy: a task payload cannot pick the host.

`LLM_ENDPOINT` is the only trusted origin. A per-task `endpoint` is accepted
only when it matches it; with no configured endpoint there is nothing to match,
so the override is refused instead of turning the solver into an SSRF sink.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from captcha.domain.errors import ConfigError
from captcha.solvers.ocr._llm import resolve_endpoint


def _settings(endpoint: str) -> SimpleNamespace:
    return SimpleNamespace(llm_endpoint=endpoint)


def test_override_without_a_configured_endpoint_is_refused() -> None:
    with pytest.raises(ConfigError):
        resolve_endpoint(_settings(""), {"endpoint": "http://10.0.0.1:8080"})


def test_override_of_a_metadata_host_is_refused_even_without_a_configured_endpoint() -> None:
    with pytest.raises(ConfigError):
        resolve_endpoint(_settings(""), {"endpoint": "http://169.254.169.254:80"})


def test_override_on_the_configured_origin_is_allowed() -> None:
    assert resolve_endpoint(_settings("http://127.0.0.1:11434"), {"endpoint": "http://127.0.0.1:11434"}) == (
        "http://127.0.0.1:11434/v1/chat/completions"
    )


def test_override_on_another_origin_is_refused() -> None:
    with pytest.raises(ConfigError):
        resolve_endpoint(_settings("http://127.0.0.1:11434"), {"endpoint": "http://10.0.0.2:11434"})


def test_a_configured_endpoint_needs_no_override() -> None:
    assert resolve_endpoint(_settings("http://127.0.0.1:11434"), {}) == "http://127.0.0.1:11434/v1/chat/completions"
