"""MTCaptcha wire: request params, the transaction signature, and parsing.

Flow (all GET, JSON replies under `result`):

  1. `getchallenge.json` -> `result.challenge` with `ct` (challenge token) and
     an optional `foldChlg` proof-of-work; `result.challenge.tid` is informational.
  2. `getimage.json`     -> `result.img.image64` (the text puzzle image).
  3. CV/OCR the image, then `solvechallenge.json` with `ct`, `st` = answer,
     `fa`/`kt`/`fs` from the fold challenge -> `result.verifyResult`:
     `{isVerified, verifiedToken: {vt}}`. `vt` is the `v1(...)` token the site
     submits; the server builds it, so the solver never composes one by hand.

`tsh` is `"TH[" + md5("mtcap@mtcaptcha.com" + sitekey) + "]"` — the iframe's
`transactionSignature(sitekey, "mtcap@mtcaptcha.com")`. The literal shape is
pinned by the 2026-10 build and by the 2021 client's `TH[...]` constant.

Nothing here is a live-verified constant: the endpoint names and the response
keys come from the bundle, the optional cookies are the widget's own, and
`jsV`/theme are passed through rather than guessed. `experimental=True` stays
until an L3 canary (contract §10).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

from captcha.domain.errors import CaptchaError, ChallengeExpiredError

from . import endpoints as ep
from .fold import fold_params

#: Default challenge text length advertised by the widget (`tl`).
TEXT_LENGTH = 10


@dataclass(frozen=True)
class Challenge:
    """What `getchallenge.json` tells us before the image is fetched."""

    ct: str
    session_id: str
    fold: dict[str, Any] | None
    language: str
    text_length: int


def session_id() -> str:
    return str(uuid4())


def transaction_signature(sitekey: str, salt: str = ep.TRANSACTION_SALT) -> str:
    digest = hashlib.md5((salt + sitekey).encode("utf-8")).hexdigest()
    return f"TH[{digest}]"


def domain_of(site_url: str) -> str:
    """Bare host for `bd`: no scheme, path or port."""
    host = urlparse(site_url).hostname or site_url.split("://", 1)[-1].split("/", 1)[0]
    return host.lower()


def iframe_referer(sitekey: str, domain: str, *, language: str) -> str:
    query = (
        f"v={ep.JS_VERSION}&sitekey={quote(sitekey)}&iframeId=mtcaptcha-iframe-1"
        f"&widgetSize=standard&custom=false&widgetInstance=mtcaptcha&challengeType=standard"
        f"&theme=basic&lang={quote(language)}&action=&autoFadeOuterText=false"
        f"&host={quote('https://' + domain)}&hostname={quote(domain)}"
        f"&serviceDomain=service.mtcaptcha.com&textLength={TEXT_LENGTH}"
        "&lowFrictionInvisible=&enableMouseFlow=false"
    )
    return f"{ep.IFRAME}?{query}"


def headers_for(sitekey: str, domain: str, *, language: str) -> dict[str, str]:
    """Referer/Origin as the widget's own iframe sends them."""
    return {
        "Referer": iframe_referer(sitekey, domain, language=language),
        "Origin": "https://service.mtcaptcha.com",
    }


def challenge_params(
    sitekey: str,
    domain: str,
    *,
    session: str,
    language: str,
    text_length: int = TEXT_LENGTH,
    action: str = "",
    now_ms: int | None = None,
) -> dict[str, str]:
    return {
        "sk": sitekey,
        "bd": domain,
        "rt": str(int(now_ms if now_ms is not None else time.time() * 1000)),
        "tsh": transaction_signature(sitekey),
        "act": action or "$",
        "ss": session,
        "lf": "0",
        "tl": str(text_length),
        "lg": language,
        "tp": "s",
    }


def image_params(sitekey: str, *, ct: str, session: str, fold: dict[str, Any] | None) -> dict[str, str]:
    fa, _, _ = fold_params(fold)
    return {"sk": sitekey, "ct": ct, "fa": fa, "ss": session}


def solve_params(
    sitekey: str,
    domain: str,
    *,
    ct: str,
    session: str,
    answer: str,
    fold: dict[str, Any] | None,
    language: str,
    text_length: int = TEXT_LENGTH,
    action: str = "",
    now_ms: int | None = None,
) -> dict[str, str]:
    fa, kt, fs = fold_params(fold)
    return {
        "ct": ct,
        "sk": sitekey,
        "st": answer,
        "lf": "0",
        "bd": domain,
        "rt": str(int(now_ms if now_ms is not None else time.time() * 1000)),
        "tsh": transaction_signature(sitekey),
        "fa": fa,
        "qh": "$",
        "act": action or "$",
        "ss": session,
        "tl": str(text_length),
        "lg": language,
        "tp": "s",
        "kt": kt,
        "fs": fs,
    }


def cookie_header(pulse_value: str) -> str:
    return f"mtv1Pulse={pulse_value}; mtv1ConfSum={ep.CONF_SUM}; jsV={ep.JS_VERSION}"


def json_object(reply: Any, what: str) -> dict[str, Any]:
    """Decode a JSON object reply; a non-200/non-JSON answer is a spent challenge."""
    status = getattr(reply, "status", None)
    if status is not None and int(status) != 200:
        raise ChallengeExpiredError(f"mtcaptcha {what}: HTTP {status}", context={"status": status})
    try:
        data = reply.json()
    except Exception as exc:
        raise ChallengeExpiredError(
            f"mtcaptcha {what} returned non-JSON",
            context={"status": status},
        ) from exc
    if not isinstance(data, dict):
        raise ChallengeExpiredError(f"mtcaptcha {what}: reply is not an object", context={"status": status})
    return data


def _result(data: dict[str, Any], what: str) -> dict[str, Any]:
    result = data.get("result")
    if not isinstance(result, dict):
        raise ChallengeExpiredError(
            f"mtcaptcha {what}: no result object",
            context={"keys": sorted(data.keys())[:20]},
        )
    return result


def parse_challenge(data: dict[str, Any], *, session: str, language: str, text_length: int) -> Challenge:
    result = _result(data, "getchallenge")
    challenge = result.get("challenge")
    if not isinstance(challenge, dict) or not challenge.get("ct"):
        raise ChallengeExpiredError(
            "mtcaptcha getchallenge: no challenge token",
            context={"keys": sorted(result.keys())[:20]},
        )
    # `hasFoldChlg` is authoritative when present; older replies omit it but
    # still carry `foldChlg`, so a missing flag falls back to the object.
    flag = challenge.get("hasFoldChlg")
    fold = challenge.get("foldChlg") if (flag if isinstance(flag, bool) else bool(challenge.get("foldChlg"))) else None
    return Challenge(
        ct=str(challenge["ct"]),
        session_id=session,
        fold=fold if isinstance(fold, dict) else None,
        language=language,
        text_length=text_length,
    )


def parse_image(data: dict[str, Any]) -> bytes:
    result = _result(data, "getimage")
    img = result.get("img")
    raw = img.get("image64") if isinstance(img, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise ChallengeExpiredError(
            "mtcaptcha getimage: no image",
            context={"keys": sorted(result.keys())[:20]},
        )
    payload = "".join(raw.strip().split())
    if payload.startswith("data:"):
        payload = payload.partition(",")[2]
    padded = payload + "=" * ((-len(payload)) % 4)
    try:
        body = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CaptchaError("mtcaptcha getimage: image is not base64", context={"error": str(exc)[:120]}) from exc
    if not body:
        raise ChallengeExpiredError("mtcaptcha getimage: empty image")
    return body


def parse_token(data: dict[str, Any]) -> str:
    """`verifyResult.verifiedToken.vt` — the token the site submits."""
    result = _result(data, "solvechallenge")
    verify = result.get("verifyResult")
    if not isinstance(verify, dict) or not verify.get("isVerified"):
        context = {
            "code": verify.get("code") if isinstance(verify, dict) else None,
            "message": verify.get("msg") if isinstance(verify, dict) else None,
        }
        raise ChallengeExpiredError("mtcaptcha: verifier rejected the answer", context=context)
    token = verify.get("verifiedToken")
    vt = token.get("vt") if isinstance(token, dict) else None
    if not isinstance(vt, str) or not vt.startswith("v1("):
        raise CaptchaError(
            "mtcaptcha: verified reply carried no v1(...) token",
            context={"keys": sorted(verify.keys())[:20]},
        )
    return vt
