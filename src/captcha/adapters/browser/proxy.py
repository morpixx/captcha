"""Proxy URL helpers.

The provider receives a proxy as a client-supplied URL and hands Camoufox the
structured dict playwright wants. The GeoIP probe needs a URL again, and that
URL carries credentials, so they are percent-encoded here: a password with
`@`, `:` or `/` must not break the parse or the tunnel. The rebuilt URL is
never logged.
"""

from __future__ import annotations

import hashlib
from urllib.parse import quote


def parse_proxy(url: str) -> dict[str, str]:
    """Normalize `scheme://user:pass@host:port` or `host:port` into playwright's shape."""
    url = url.strip()
    if not url:
        raise ValueError("proxy url is empty")
    if "://" not in url:
        return {"server": "http://" + url}
    scheme, _, rest = url.partition("://")
    userpass = ""
    hostpart = rest
    # rpartition: the password is the part allowed to contain '@'.
    if "@" in rest:
        userpass, _, hostpart = rest.rpartition("@")
    proxy: dict[str, str] = {"server": f"{scheme}://{hostpart}"}
    if userpass:
        user, _, secret = userpass.partition(":")
        proxy["username"] = user
        if secret:
            proxy["password"] = secret
    return proxy


def proxy_url(proxy: dict[str, str] | None) -> str | None:
    """Rebuild a proxy URL with percent-encoded credentials (probe-only)."""
    if not proxy:
        return None
    server = str(proxy.get("server") or "").strip()
    if not server:
        raise ValueError("proxy.server is empty")
    username = proxy.get("username")
    secret = proxy.get("password")
    if username is None and secret is None:
        return server
    scheme, sep, rest = server.partition("://")
    if not sep:
        raise ValueError("proxy.server must include a scheme")
    quoted_user = quote(str(username or ""), safe="")
    quoted_secret = quote(str(secret or ""), safe="")
    return f"{scheme}://{quoted_user}:{quoted_secret}@{rest}"


def proxy_fingerprint(proxy: dict[str, str] | None) -> str:
    """Stable cache key for a proxy without keeping the credentials as a key."""
    if not proxy:
        return ""
    raw = "|".join(str(proxy.get(name) or "") for name in ("server", "username", "password"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["parse_proxy", "proxy_fingerprint", "proxy_url"]
