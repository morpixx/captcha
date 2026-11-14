"""One proxy entry. Frozen; mutation lives in Pool state, not here."""

from __future__ import annotations

from urllib.parse import quote

import msgspec


class Proxy(msgspec.Struct, frozen=True, kw_only=True):
    """Single proxy endpoint.

    `url` is the full httpx-compatible form: `http://user:pass@host:port`
    or `socks5://...`. Credentials may be embedded there OR provided in
    `username`/`password` and composed at pick time via `with_session`.
    """

    url: str
    provider: str
    country: str | None = None
    session_id: str | None = None
    username: str | None = None
    password: str | None = None
    tags: dict[str, str] = msgspec.field(default_factory=dict)

    def with_session(self, session_id: str) -> "Proxy":
        """Return a copy pinned to a session id. Auth is re-urlencoded."""
        host_port = self.url.split("@")[-1]
        user = self.username or ""
        pwd = self.password or ""
        # Sticky-session convention is "<user>-session-<id>" for most
        # residential providers; subclasses of Pool can swap this via
        # providers.<Vendor>.build_auth if the tag differs.
        user_tagged = f"{user}-session-{session_id}" if user else session_id
        scheme = self.url.split("://", 1)[0]
        auth = f"{quote(user_tagged, safe='')}:{quote(pwd, safe='')}"
        return msgspec.structs.replace(
            self,
            url=f"{scheme}://{auth}@{host_port}" if user_tagged or pwd else f"{scheme}://{host_port}",
            session_id=session_id,
        )
