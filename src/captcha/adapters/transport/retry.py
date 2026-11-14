"""Retry policy. Transport flakes retry, antibot signals don't."""

from __future__ import annotations

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

# 403/429/451 are antibot flags — the client raises ChallengeExpiredError for
# them BEFORE tenacity ever sees the response, so they can't retry here. This
# policy only catches transport-layer flakes: dead proxies, half-open sockets,
# TLS resets, DNS hiccups.
_TRANSPORT_EXC: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def build_retry_policy(
    *,
    attempts: int = 3,
    initial: float = 0.5,
    maximum: float = 8.0,
) -> AsyncRetrying:
    """Async retry policy for outbound HTTP. Reinstantiate per call-site —
    AsyncRetrying holds per-call state, sharing one across tasks breaks."""
    return AsyncRetrying(
        retry=retry_if_exception_type(_TRANSPORT_EXC),
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=initial, max=maximum),
        reraise=True,
    )
