"""hCaptcha site profiles: host -> sitekey, enterprise flags, and lane mode.

A profile is what we know about one deployment of the widget. mode is the lane
policy, not a hint: Riot's sitekeys refuse the HTTP path (the server answers
with a fresh "hsw" challenge instead of a P1_ pass token), so those profiles are
SolveMode.BROWSER. Steam and Epic are served by the browser today and their
HTTP agent is separate work, so they stay mode="auto". Anything unrecognised
also falls back to "auto": the browser lane can still drive it, and a task must
not be refused before a solver had a chance to try.

Values come from the reverse-engineering notes (D:/riot-research, read-only) and
live widget captures. A sitekey nobody captured is None with a FILL marker —
never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from captcha.domain import CaptchaType, SolveMode

from .common import truthy

#: hCaptcha's own bundle. Fetched inside the page and evaluated there (see
#: common.EXECUTE_JS); never a client-supplied URL.
DEFAULT_MINT_URL = "https://js.hcaptcha.com/1/api.js?render=explicit"


@dataclass(frozen=True, slots=True)
class Site:
    """One hCaptcha deployment: where it runs, which key it uses, which lane may answer."""

    name: str
    host: str
    sitekey: str | None = None
    enterprise: bool = False
    rqdata: str | None = None
    invisible: bool = True
    mint_url: str = DEFAULT_MINT_URL
    mode: SolveMode = SolveMode.AUTO

    @property
    def allows_browser(self) -> bool:
        return self.mode is not SolveMode.HTTP


SITES: tuple[Site, ...] = (
    # Riot login: invisible widget on the OAuth page. HTTP is refused by design.
    Site(
        name="riot-authenticate",
        host="authenticate.riotgames.com",
        sitekey="a5f74b19-9e45-40e0-b45d-47ff91b7a6c2",
        invisible=True,
        mode=SolveMode.BROWSER,
    ),
    # Riot account (email change): same flow, different sitekey.
    Site(
        name="riot-account",
        host="account.riotgames.com",
        sitekey="b3c619fc-4b72-4838-9bf2-398888588d62",
        invisible=True,
        mode=SolveMode.BROWSER,
    ),
    Site(
        name="steam-community",
        host="steamcommunity.com",
        sitekey=None,  # FILL: capture from the join/login widget (docs/hcaptcha-sites.md)
        invisible=False,
        mode=SolveMode.AUTO,
    ),
    Site(
        name="steam-store",
        host="store.steampowered.com",
        sitekey=None,  # FILL: confirm it is the same widget as steam-community
        invisible=False,
        mode=SolveMode.AUTO,
    ),
    Site(
        name="epic",
        host="epicgames.com",
        sitekey=None,  # FILL: capture from accounts.epicgames.com
        invisible=False,
        mode=SolveMode.AUTO,
    ),
)

_BY_HOST: dict[str, Site] = {site.host: site for site in SITES}


def all_sites() -> tuple[Site, ...]:
    """Current profiles, including ones installed through register_site()."""
    return SITES


def register_site(site: Site) -> None:
    """Install or replace a profile at runtime (deploy/config hook, not a task field)."""
    global SITES
    _BY_HOST[site.host] = site
    SITES = (*tuple(existing for existing in SITES if existing.host != site.host), site)


def find_site(host: str | None) -> Site | None:
    """Match a hostname against the profiles, including subdomains."""
    name = (host or "").strip().lower().rstrip(".")
    if not name:
        return None
    if name in _BY_HOST:
        return _BY_HOST[name]
    for known, site in _BY_HOST.items():
        if name.endswith("." + known):
            return site
    return None


def host_of(url: str | None) -> str:
    """Hostname of a page URL; "" when it cannot be parsed."""
    if not url:
        return ""
    try:
        parsed = urlsplit(url if "//" in url else "//" + url)
    except ValueError:
        return ""
    return (parsed.hostname or "").lower()


def host_matches(actual: str | None, expected: str | None) -> bool:
    """Same site, allowing a subdomain on either side (www. vs apex)."""
    got = (actual or "").strip().lower().rstrip(".")
    want = (expected or "").strip().lower().rstrip(".")
    if not got or not want:
        return False
    return got == want or got.endswith("." + want) or want.endswith("." + got)


def resolve_site(
    *,
    site_url: str | None,
    sitekey: str | None = None,
    payload: dict | None = None,
    captcha_type: CaptchaType | None = None,
) -> Site:
    """Profile for a task, with the client's per-task fields layered on top.

    Client overrides are limited to what actually varies per task: sitekey,
    rqdata, enterprise, invisible. mint_url is deliberately not overridable —
    it is a script evaluated in the page, so a task must never be able to point
    it at an arbitrary URL.
    """
    body = payload or {}
    host = host_of(site_url) or _text(body.get("host")) or ""
    base = find_site(host) or Site(name=f"unknown:{host or 'unknown'}", host=host)

    rqdata = _rqdata(body) or base.rqdata
    enterprise = (
        base.enterprise
        or captcha_type is CaptchaType.HCAPTCHA_ENTERPRISE
        or truthy(body.get("enterprise"))
        or bool(rqdata)
    )
    site_key = _text(sitekey) or _text(body.get("sitekey")) or _text(body.get("websiteKey")) or base.sitekey
    return replace(
        base,
        sitekey=site_key,
        enterprise=enterprise,
        rqdata=rqdata,
        invisible=_invisible(body, base.invisible),
    )


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _rqdata(payload: dict) -> str | None:
    """2captcha carries enterprise rqdata as rqdata or enterprisePayload."""
    for key in ("rqdata", "rq_data"):
        value = _text(payload.get(key))
        if value:
            return value
    enterprise_payload = payload.get("enterprisePayload")
    if isinstance(enterprise_payload, dict):
        return _text(enterprise_payload.get("rqdata")) or _text(enterprise_payload.get("rq_data"))
    return _text(enterprise_payload)


def _invisible(payload: dict, default: bool) -> bool:
    for key in ("isInvisible", "invisible"):
        if payload.get(key) is not None:
            return truthy(payload[key])
    return default
