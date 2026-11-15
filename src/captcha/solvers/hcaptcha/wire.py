"""hCaptcha wire protocol: checksiteconfig -> n-token -> getcaptcha.

Ported from the reference client D:/riot-research/hcaptcha_http.py, which was
verified against the live browser build:

    POST /checksiteconfig?v=<build>&host=&sitekey=&sc=1&swa=1&spst=1
        -> {"c": {"type": "hsw"|"hsj", "req": "<jwt>"}, "features": {...}, "pass": bool}

    n-token = bundle(challenge.req, {href, ardata: null, vm_data, uj_data})
    the bundle name and the asset path are the JWT "n" and "l" claims.

    POST /getcaptcha/<sitekey>
        enc_get_req: msgpack([json(c), Ext(18, nonce || ciphertext || tag)])
        plaintext:   form v/sitekey/host/hl/motionData/n/c/pst

Everything here is pure request/response shaping: no retries, no policy. The
solver in http.py decides what a challenge or a rejection means.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import msgspec

from captcha.domain import Task
from captcha.domain.errors import SensorError, UnsolvableError

API = "https://api.hcaptcha.com"
CHECK_SITE_CONFIG = f"{API}/checksiteconfig"
GET_CAPTCHA = f"{API}/getcaptcha"
REFERER = "https://newassets.hcaptcha.com/"

#: Build id hCaptcha's api.js hands to checksiteconfig. Update when the wire
#: rejects it; the reference scrapes it from api.js, one more live call we do
#: not want inside the solve path.
DEFAULT_VERSION = "10dd112850fe5c29df1e51ddc05fdfb8b5f6dbae"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 30.0
EXT_HC_ENCRYPTED_BODY = 18


class BundleRunner(Protocol):
    """The JS side of the n-token, injected as ctx.extra["hcaptcha_js"]."""

    async def solve(
        self,
        *,
        req: str,
        host: str,
        href: str,
        config: dict[str, Any],
        vm_data: str | None = None,
    ) -> str: ...

    async def encrypt(self, req: str, plaintext: bytes, config: dict[str, Any]) -> bytes: ...

    async def decrypt(self, req: str, blob: bytes, config: dict[str, Any]) -> bytes: ...


@dataclass(frozen=True, slots=True)
class Params:
    sitekey: str
    host: str
    href: str
    rqdata: str | None
    user_agent: str
    version: str
    vm_data: str | None

    def bundle_config(self) -> dict[str, Any]:
        return {"userAgent": self.user_agent}


def params_from_task(task: Task) -> Params:
    payload = task.payload if isinstance(task.payload, dict) else {}
    sitekey = _first_str(task.sitekey, payload.get("sitekey"), payload.get("siteKey"))
    page_url = _first_str(payload.get("pageurl"), payload.get("page_url"), payload.get("site_url"), task.site_url)
    host = _first_str(payload.get("host"))
    if host is None and page_url:
        host = urlparse(page_url).hostname
    if not sitekey:
        raise UnsolvableError("hcaptcha: sitekey missing", context={"task": str(task.id)})
    if not host:
        raise UnsolvableError("hcaptcha: host missing (need host or pageurl)", context={"task": str(task.id)})
    return Params(
        sitekey=sitekey,
        host=host,
        href=page_url or f"https://{host}/",
        rqdata=_first_str(payload.get("rqdata"), payload.get("enterprisePayload"), payload.get("rq_data")),
        user_agent=_first_str(task.user_agent, payload.get("userAgent"), payload.get("user_agent")) or DEFAULT_UA,
        version=_first_str(payload.get("client_version"), payload.get("clientVersion")) or DEFAULT_VERSION,
        vm_data=_first_str(payload.get("vm_data"), payload.get("vmData")),
    )


async def checksite_config(http: Any, params: Params) -> tuple[dict[str, Any], dict[str, Any]]:
    query = {
        "v": params.version,
        "host": params.host,
        "sitekey": params.sitekey,
        "sc": "1",
        "swa": "1",
        "spst": "1",
    }
    if params.rqdata:
        query["rqdata"] = params.rqdata
    resp = await http.post(
        CHECK_SITE_CONFIG,
        params=query,
        data=b"",
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": params.user_agent,
            "Accept": "application/json",
            "Content-Type": "text/plain",
            "Referer": REFERER,
        },
    )
    if resp.status != 200:
        raise SensorError(
            f"hcaptcha: checksiteconfig HTTP {resp.status}",
            context={"sitekey": params.sitekey, "host": params.host},
        )
    try:
        info = resp.json()
    except ValueError as exc:
        raise SensorError("hcaptcha: checksiteconfig returned non-JSON", context={"host": params.host}) from exc
    if not isinstance(info, dict) or not isinstance(info.get("c"), dict):
        raise UnsolvableError(
            "hcaptcha: checksiteconfig returned no challenge",
            context={"sitekey": params.sitekey, "keys": sorted(info) if isinstance(info, dict) else []},
        )
    return info["c"], info.get("features") or {}


async def get_captcha(
    http: Any,
    params: Params,
    challenge: dict[str, Any],
    features: dict[str, Any],
    n_token: str,
    req: str,
    runner: BundleRunner,
) -> dict[str, Any]:
    headers = {
        "User-Agent": params.user_agent,
        "Accept": "application/json, application/octet-stream",
        "Referer": REFERER,
        "Origin": "https://newassets.hcaptcha.com",
    }
    body: Any
    payload = body_payload(params, n_token)
    if features.get("enc_get_req"):
        sealed = await runner.encrypt(req, msgspec.msgpack.encode(payload), params.bundle_config())
        challenge_json = json.dumps(challenge, separators=(",", ":"))
        body = msgspec.msgpack.encode([challenge_json, msgspec.msgpack.Ext(EXT_HC_ENCRYPTED_BODY, sealed)])
        headers["Content-Type"] = "application/octet-stream"
    else:
        form = {k: v for k, v in payload.items() if v is not None}
        form["c"] = json.dumps(challenge, separators=(",", ":"))
        form["pst"] = "false"
        body = form
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    resp = await http.post(
        f"{GET_CAPTCHA}/{params.sitekey}",
        headers=headers,
        data=body,
        timeout=HTTP_TIMEOUT,
    )
    if resp.status != 200:
        raise SensorError(
            f"hcaptcha: getcaptcha HTTP {resp.status}",
            context={"sitekey": params.sitekey, "host": params.host},
        )

    ctype = (resp.header("content-type") or "").lower()
    try:
        if "json" in ctype or (not ctype and resp.body[:1] == b"{"):
            data: Any = resp.json()
        elif resp.body:
            plain = await runner.decrypt(req, resp.body, params.bundle_config())
            data = msgspec.msgpack.decode(plain)
        else:
            data = None
    except (ValueError, msgspec.DecodeError) as exc:
        raise SensorError("hcaptcha: cannot decode getcaptcha response", context={"host": params.host}) from exc
    if not isinstance(data, dict):
        raise SensorError(
            "hcaptcha: unexpected getcaptcha response",
            context={"sitekey": params.sitekey, "type": type(data).__name__},
        )
    return data


def body_payload(params: Params, n_token: str) -> dict[str, Any]:
    now = int(time.time() * 1000)
    motion = {"st": now - 2500, "dct": now - 900, "mm": [], "md": [], "mu": [], "v": 1}
    payload: dict[str, Any] = {
        "v": params.version,
        "sitekey": params.sitekey,
        "host": params.host,
        "hl": "en",
        "motionData": json.dumps(motion, separators=(",", ":")),
        "n": n_token,
        "e": None,
    }
    if params.rqdata:
        payload["rqdata"] = params.rqdata
    return payload


def pass_token(data: dict[str, Any]) -> str | None:
    token = data.get("generated_pass_UUID")
    if isinstance(token, str) and token.startswith("P1_"):
        return token
    fallback = data.get("token")
    if data.get("pass") and isinstance(fallback, str) and fallback:
        return fallback
    return None


def is_challenge(data: dict[str, Any]) -> bool:
    if data.get("pass"):
        return False
    c = data.get("c")
    return isinstance(c, dict) and isinstance(c.get("req"), str)


def challenge_kind(data: dict[str, Any]) -> str | None:
    c = data.get("c")
    kind = c.get("type") if isinstance(c, dict) else None
    return kind if isinstance(kind, str) else None


def response_key(data: dict[str, Any], challenge: dict[str, Any]) -> str | None:
    return _first_str(data.get("key"), challenge.get("key"))


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None
