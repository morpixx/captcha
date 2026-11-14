"""Response envelope returned by Client. msgspec.Struct for cheap construction."""

from __future__ import annotations

import json as _json
from typing import Any

import msgspec


class Response(msgspec.Struct, kw_only=True):
    """HTTP response, decoded lazily. headers is case-preserving list of (k, v)
    tuples — a server can repeat Set-Cookie and we must not collapse them."""

    status: int
    headers: list[tuple[str, str]]
    body: bytes
    elapsed_ms: float
    final_url: str
    ja3_hint: str = ""

    @property
    def text(self) -> str:
        """Lazy decode. Honors charset from Content-Type, falls back to utf-8
        with replacement — never raises mid-pipeline on a mis-declared page."""
        charset = _charset_from_headers(self.headers) or "utf-8"
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self, **kwargs: Any) -> Any:
        """Parse body as JSON. kwargs forwarded to json.loads."""
        return _json.loads(self.body, **kwargs)

    def header(self, name: str) -> str | None:
        """Case-insensitive single-value lookup. Last-wins on duplicates."""
        lower = name.lower()
        hit: str | None = None
        for k, v in self.headers:
            if k.lower() == lower:
                hit = v
        return hit

    def headers_all(self, name: str) -> list[str]:
        """All values for a header name (needed for Set-Cookie)."""
        lower = name.lower()
        return [v for k, v in self.headers if k.lower() == lower]


def _charset_from_headers(headers: list[tuple[str, str]]) -> str | None:
    for k, v in headers:
        if k.lower() != "content-type":
            continue
        for part in v.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                return part.split("=", 1)[1].strip().strip('"').strip("'")
    return None
