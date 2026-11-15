"""Redaction of task secrets before they are persisted or dead-lettered.

Three consumers, one implementation:

- the gateway strips proxy/cookie fields from the Postgres copy of a payload;
- the queue adapter strips them from anything written to the dead-letter stream;
- `redact_dsn` keeps datastore passwords out of log lines when a worker prints
  the Redis/Postgres URL it connected to.

Pure dict work, no I/O, stdlib + msgspec only — the adapter may not import the
services layer, so the helper lives in domain where both can reach it.

Sensitive keys are `proxy*` (`proxy`, `proxyType`, `proxyAddress`, `proxyPort`,
`proxyLogin`, `proxyPassword`, legacy `proxytypeaddr`) and any cookie field, at
any nesting depth. Values are dropped rather than masked: nothing downstream
reads them back, and a masked value still leaks shape and length.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import msgspec

from captcha.domain.task import TaskEnvelope

#: Key prefixes that mark a credential. Case-insensitive.
SENSITIVE_PREFIXES: tuple[str, ...] = ("proxy",)

#: Exact (lower-cased) key names that mark a credential without a prefix.
SENSITIVE_KEYS: frozenset[str] = frozenset({"cookie", "cookies", "proxies"})


def is_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    return lowered.startswith(SENSITIVE_PREFIXES) or lowered in SENSITIVE_KEYS


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return redact_payload(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def redact_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a copy of `payload` with every credential-bearing key removed."""
    if not payload:
        return {}
    return {str(key): _redact_value(value) for key, value in payload.items() if not is_sensitive_key(str(key))}


def redact_envelope(envelope: TaskEnvelope) -> TaskEnvelope:
    """Copy of a queue envelope safe to persist in the DLQ.

    `Task.proxy` is the composed proxy string, so it goes to None; the payload
    loses proxy*/cookies keys recursively. The result is a new envelope — the
    original still owns the full payload for the live stream.
    """
    task = msgspec.structs.replace(
        envelope.task,
        proxy=None,
        payload=redact_payload(envelope.task.payload),
    )
    return msgspec.structs.replace(envelope, task=task)


def safe_url(url: str | None) -> str:
    """`scheme://host[:port]` only: no userinfo, path, query or fragment.

    For log lines and error context built from URLs that may carry a token in
    the query (`pageurl`, a challenge URL, a callback). A string that is not a
    URL at all becomes `<unparseable>` instead of being echoed.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "<unparseable>"
    if not parts.scheme or not parts.hostname:
        return "<unparseable>"
    try:
        port = parts.port
    except ValueError:
        port = None
    hostname = parts.hostname
    if ":" in hostname:  # IPv6 literal: keep the brackets so the line stays parseable
        hostname = f"[{hostname}]"
    return f"{parts.scheme}://{hostname}" + (f":{port}" if port else "")


def redact_dsn(url: str | None) -> str:
    """A connection URL with its userinfo and query stripped, for log lines.

    `redis://:secret@cache:6379/0` becomes `redis://***@cache:6379/0`; the path
    (database name) survives, the password and every query parameter do not. A
    string that is not a scheme://host URL is replaced with `***` rather than
    logged, because there is no safe way to tell where its secret is.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "***"
    if not parts.scheme or not parts.hostname:
        return "***"
    try:
        port = parts.port
    except ValueError:
        port = None
    hostname = parts.hostname
    if ":" in hostname:  # IPv6 literal: keep the brackets so the line stays parseable
        hostname = f"[{hostname}]"
    host = f"{hostname}:{port}" if port else hostname
    userinfo = "***@" if (parts.username is not None or parts.password is not None) else ""
    return f"{parts.scheme}://{userinfo}{host}{parts.path}"


__all__ = [
    "SENSITIVE_KEYS",
    "SENSITIVE_PREFIXES",
    "is_sensitive_key",
    "redact_dsn",
    "redact_envelope",
    "redact_payload",
    "safe_url",
]
