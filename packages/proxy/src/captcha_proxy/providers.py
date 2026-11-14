"""Vendor adapters — URL/auth builders only.

None of these talk to the vendor's API; they assemble the auth string a
residential provider expects for geo targeting, sticky sessions and
session ids. Plug a vendor's real control-plane client under `# FILL`
when you need pool resize / live stats.
"""

from __future__ import annotations

from dataclasses import dataclass

from captcha_proxy.proxy import Proxy


@dataclass(slots=True)
class BrightData:
    customer: str
    zone: str
    password: str
    host: str = "brd.superproxy.io"
    port: int = 22225

    def build(self, *, country: str | None = None, session_id: str | None = None) -> Proxy:
        parts = [f"brd-customer-{self.customer}", f"zone-{self.zone}"]
        if country:
            parts.append(f"country-{country.lower()}")
        if session_id:
            parts.append(f"session-{session_id}")
        user = "-".join(parts)
        url = f"http://{user}:{self.password}@{self.host}:{self.port}"
        return Proxy(
            url=url,
            provider="brightdata",
            country=country,
            session_id=session_id,
            username=user,
            password=self.password,
        )


@dataclass(slots=True)
class Oxylabs:
    username: str
    password: str
    host: str = "pr.oxylabs.io"
    port: int = 7777

    def build(self, *, country: str | None = None, session_id: str | None = None) -> Proxy:
        parts = [f"customer-{self.username}"]
        if country:
            parts.append(f"cc-{country.upper()}")
        if session_id:
            parts.append(f"sessid-{session_id}")
        user = "-".join(parts)
        url = f"http://{user}:{self.password}@{self.host}:{self.port}"
        return Proxy(
            url=url,
            provider="oxylabs",
            country=country,
            session_id=session_id,
            username=user,
            password=self.password,
        )


@dataclass(slots=True)
class Smartproxy:
    username: str
    password: str
    host: str = "gate.smartproxy.com"
    port: int = 10000

    def build(self, *, country: str | None = None, session_id: str | None = None) -> Proxy:
        parts = [f"user-{self.username}"]
        if country:
            parts.append(f"country-{country.lower()}")
        if session_id:
            parts.append(f"session-{session_id}")
        user = "-".join(parts)
        url = f"http://{user}:{self.password}@{self.host}:{self.port}"
        return Proxy(
            url=url,
            provider="smartproxy",
            country=country,
            session_id=session_id,
            username=user,
            password=self.password,
        )


@dataclass(slots=True)
class IPRoyal:
    username: str
    password: str
    host: str = "geo.iproyal.com"
    port: int = 12321

    def build(self, *, country: str | None = None, session_id: str | None = None) -> Proxy:
        # IPRoyal encodes options in the password field, not the user.
        pwd_parts = [self.password]
        if country:
            pwd_parts.append(f"country-{country.lower()}")
        if session_id:
            pwd_parts.append(f"session-{session_id}")
            pwd_parts.append("lifetime-30m")
        pwd = "_".join(pwd_parts)
        url = f"http://{self.username}:{pwd}@{self.host}:{self.port}"
        return Proxy(
            url=url,
            provider="iproyal",
            country=country,
            session_id=session_id,
            username=self.username,
            password=pwd,
        )
