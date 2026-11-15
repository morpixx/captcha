"""Official 2captcha/capsolver task names and task-payload normalisation.

The JSON API is spoken by every SDK in the wild, and each spells "type" its own
way: 2captcha ships HCaptchaTaskProxyless, capsolver the same task as
HCaptchaTaskProxyLess, and hand-written clients send our internal hcaptcha.
TYPE_ALIASES is that table; resolve_captcha_type is the only entry point.

The same module folds the flat wire fields into the shapes the workers read:
proxyType/proxyAddress/proxyPort/proxyLogin/proxyPassword become a single
canonical scheme://[login:password@]host:port URL, and the hCaptcha extras
(isInvisible/invisible, enterprisePayload/rqdata, cookies, host) get their
internal names.

Pure string and dict handling: no I/O, no logging. Proxy credentials pass
through here and must never reach a log line or an error description, so the
exceptions below name a field, never a value.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from captcha.domain.enums import CaptchaType

# ---------------------------------------------------------------------------
# type names
# ---------------------------------------------------------------------------

#: Official task-object names -> internal type. Keys are matched case- and
#: separator-insensitively (see resolve_captcha_type), so ReCaptchaV2Task and
#: recaptcha_v2_task both land on the same entry. Every CaptchaType value is
#: accepted implicitly as well, which covers our own snake_case names.
TYPE_ALIASES: dict[str, CaptchaType] = {
    # hCaptcha. 2captcha and capsolver disagree on the word order of the
    # enterprise variants, so both orders are listed.
    "HCaptchaTask": CaptchaType.HCAPTCHA,
    "HCaptchaTaskProxyless": CaptchaType.HCAPTCHA,
    "HCaptchaTaskProxyLess": CaptchaType.HCAPTCHA,
    "HCaptchaTaskEnterprise": CaptchaType.HCAPTCHA_ENTERPRISE,
    "HCaptchaTaskEnterpriseProxyless": CaptchaType.HCAPTCHA_ENTERPRISE,
    "HCaptchaTaskEnterpriseProxyLess": CaptchaType.HCAPTCHA_ENTERPRISE,
    "HCaptchaEnterpriseTask": CaptchaType.HCAPTCHA_ENTERPRISE,
    "HCaptchaEnterpriseTaskProxyless": CaptchaType.HCAPTCHA_ENTERPRISE,
    "HCaptchaEnterpriseTaskProxyLess": CaptchaType.HCAPTCHA_ENTERPRISE,
    # reCAPTCHA v2
    "RecaptchaV2Task": CaptchaType.RECAPTCHA_V2,
    "RecaptchaV2TaskProxyless": CaptchaType.RECAPTCHA_V2,
    "ReCaptchaV2Task": CaptchaType.RECAPTCHA_V2,
    "ReCaptchaV2TaskProxyLess": CaptchaType.RECAPTCHA_V2,
    # reCAPTCHA v2 enterprise
    "RecaptchaV2EnterpriseTask": CaptchaType.RECAPTCHA_V2_ENTERPRISE,
    "RecaptchaV2EnterpriseTaskProxyless": CaptchaType.RECAPTCHA_V2_ENTERPRISE,
    "ReCaptchaV2EnterpriseTask": CaptchaType.RECAPTCHA_V2_ENTERPRISE,
    "ReCaptchaV2EnterpriseTaskProxyLess": CaptchaType.RECAPTCHA_V2_ENTERPRISE,
    # reCAPTCHA v3 and v3 enterprise
    "RecaptchaV3Task": CaptchaType.RECAPTCHA_V3,
    "RecaptchaV3TaskProxyless": CaptchaType.RECAPTCHA_V3,
    "ReCaptchaV3Task": CaptchaType.RECAPTCHA_V3,
    "ReCaptchaV3TaskProxyLess": CaptchaType.RECAPTCHA_V3,
    "RecaptchaV3EnterpriseTask": CaptchaType.RECAPTCHA_V3_ENTERPRISE,
    "RecaptchaV3EnterpriseTaskProxyless": CaptchaType.RECAPTCHA_V3_ENTERPRISE,
    "ReCaptchaV3EnterpriseTask": CaptchaType.RECAPTCHA_V3_ENTERPRISE,
    "ReCaptchaV3EnterpriseTaskProxyLess": CaptchaType.RECAPTCHA_V3_ENTERPRISE,
    # Cloudflare Turnstile
    "TurnstileTask": CaptchaType.TURNSTILE,
    "TurnstileTaskProxyless": CaptchaType.TURNSTILE,
    "TurnstileTaskProxyLess": CaptchaType.TURNSTILE,
    "AntiTurnstileTask": CaptchaType.TURNSTILE,
    "AntiTurnstileTaskProxyLess": CaptchaType.TURNSTILE,
    # FunCaptcha / Arkose
    "FunCaptchaTask": CaptchaType.FUNCAPTCHA,
    "FunCaptchaTaskProxyless": CaptchaType.FUNCAPTCHA,
    "FunCaptchaTaskProxyLess": CaptchaType.FUNCAPTCHA,
    # GeeTest (the task object is the v3 one; v4 keeps the snake_case name)
    "GeeTestTask": CaptchaType.GEETEST_V3,
    "GeeTestTaskProxyless": CaptchaType.GEETEST_V3,
    "GeeTestTaskProxyLess": CaptchaType.GEETEST_V3,
    # image -> text
    "ImageToTextTask": CaptchaType.IMAGE_TO_TEXT,
    "ImageToTextTaskMugshot": CaptchaType.IMAGE_TO_TEXT,
}


def _alias_key(raw: object) -> str:
    """Case- and separator-insensitive key: HCaptchaTaskProxyLess -> hcaptchataskproxyless."""
    return "".join(ch for ch in str(raw).strip().lower() if ch.isalnum())


_TYPE_LOOKUP: dict[str, CaptchaType] = {_alias_key(name): value for name, value in TYPE_ALIASES.items()}
# Our own names are always valid: a client that already speaks snake_case must
# not need an entry in the table above.
for _member in CaptchaType:
    _TYPE_LOOKUP.setdefault(_alias_key(_member.value), _member)


class UnknownCaptchaType(ValueError):
    """A "type" that is neither an official task name nor one of our types."""

    def __init__(self, raw: object) -> None:
        self.raw = raw
        super().__init__(f"unknown captcha type: {raw!r}")


def resolve_captcha_type(raw: str | CaptchaType) -> CaptchaType:
    """Map an official (or internal) task name onto CaptchaType.

    Raises UnknownCaptchaType -- a ValueError, which pydantic turns into the
    public ERROR_BAD_REQUEST envelope -- for a name we do not sell.
    """
    if isinstance(raw, CaptchaType):
        return raw
    if isinstance(raw, str):
        resolved = _TYPE_LOOKUP.get(_alias_key(raw))
        if resolved is not None:
            return resolved
    raise UnknownCaptchaType(raw)


# ---------------------------------------------------------------------------
# proxy
# ---------------------------------------------------------------------------

#: The four schemes 2captcha documents, plus the two names curl_cffi also
#: accepts (which keep their remote-DNS meaning when forwarded).
PROXY_SCHEMES = frozenset({"http", "https", "socks4", "socks5", "socks4a", "socks5h"})

#: Wire field (lowercased) -> internal slot. Both the official camelCase and
#: the snake_case spelling clients sometimes send are covered.
_PROXY_FIELDS: dict[str, str] = {
    "proxytype": "proxy_type",
    "proxy_type": "proxy_type",
    "proxyaddress": "proxy_address",
    "proxy_address": "proxy_address",
    "proxyport": "proxy_port",
    "proxy_port": "proxy_port",
    "proxylogin": "proxy_login",
    "proxy_login": "proxy_login",
    "proxypassword": "proxy_password",
    "proxy_password": "proxy_password",
}


class ProxyFormatError(ValueError):
    """Malformed proxy fields. Messages name the field, never its value."""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _scheme(raw: Any, *, default: str | None = "http") -> str:
    text = _text(raw)
    if text is None:
        if default is None:
            raise ProxyFormatError("proxy scheme is required")
        return default
    scheme = text.lower()
    if scheme not in PROXY_SCHEMES:
        raise ProxyFormatError(f"unsupported proxy scheme {scheme!r}; expected http/https/socks4/socks5")
    return scheme


def _port(raw: Any) -> int:
    text = _text(raw)
    if text is None:
        raise ProxyFormatError("proxyPort is required")
    try:
        port = int(text)
    except ValueError:
        raise ProxyFormatError("proxyPort must be an integer") from None
    if not 1 <= port <= 65535:
        raise ProxyFormatError("proxyPort must be between 1 and 65535")
    return port


def _bracket(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _credentials(login: Any, password: Any) -> str:
    user = _text(login)
    secret = None if password is None else str(password)
    if user is None:
        if secret:
            raise ProxyFormatError("proxyPassword without proxyLogin")
        return ""
    auth = quote(user, safe="")
    if secret:
        auth += ":" + quote(secret, safe="")
    return auth + "@"


def _split_host_port(address: str) -> tuple[str, int] | None:
    """host:port out of a bare address; None when the address carries no port."""
    text = address.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.rsplit("@", 1)[-1]  # credentials glued to the address are irrelevant here
    if text.startswith("["):  # [2001:db8::1]:8080
        host, _, rest = text[1:].partition("]")
        if not rest.startswith(":"):
            return None
        return host, _port(rest[1:])
    host, separator, port_text = text.rpartition(":")
    if not separator or not host or not port_text:
        return None
    return host, _port(port_text)


def _address_host(address: str) -> str:
    """Just the host part: scheme, credentials and an embedded host:port stripped.

    A bare IPv6 literal has several colons and no port in it, so only a single
    colon followed by digits is treated as an embedded port.
    """
    text = address.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.rsplit("@", 1)[-1]
    if text.startswith("["):
        return text[1:].partition("]")[0]
    if text.count(":") == 1:
        host, _, tail = text.partition(":")
        if tail.isdigit():
            return host
    return text


def build_proxy_url(
    *,
    proxy_type: Any = None,
    address: Any = None,
    port: Any = None,
    login: Any = None,
    password: Any = None,
) -> str | None:
    """Compose scheme://[login:password@]host:port from the flat fields.

    Returns None when there is no address at all -- that is a proxyless task,
    not an error. The scheme comes from proxyType, else from a scheme glued to
    the address, else http (2captcha's default).
    """
    address_text = _text(address)
    if address_text is None:
        if _text(port) is not None or _text(login) is not None or _text(password) is not None:
            raise ProxyFormatError("proxyPort/proxyLogin/proxyPassword without proxyAddress")
        return None

    if proxy_type is not None and _text(proxy_type) is not None:
        scheme = _scheme(proxy_type)
    elif "://" in address_text:
        scheme = _scheme(address_text.split("://", 1)[0])
    else:
        scheme = "http"

    parsed = _split_host_port(address_text)
    if _text(port) is not None:
        host, number = _address_host(address_text), _port(port)
    elif parsed is not None:
        host, number = parsed
    else:
        raise ProxyFormatError("proxyAddress has no port; send proxyPort")
    return f"{scheme}://{_credentials(login, password)}{_bracket(host)}:{number}"


def normalize_proxy_string(value: str, *, default_scheme: str | None = None) -> str:
    """Canonicalise the "proxy" string field.

    Accepts the full URL 2captcha's JSON docs use and the bare
    login:password@host:port form its legacy API uses; a missing scheme is
    filled from proxyType, else http.
    """
    raw = str(value).strip()
    if not raw:
        raise ProxyFormatError("proxy is empty")
    if "://" in raw:
        scheme, rest = raw.split("://", 1)
        scheme = _scheme(scheme, default=None)
        if not rest:
            raise ProxyFormatError("proxy is missing host:port")
        if _split_host_port(rest) is None:
            raise ProxyFormatError("proxy has no port")
        return f"{scheme}://{rest}"
    if _split_host_port(raw) is None:
        raise ProxyFormatError("proxy has no port")
    return f"{_scheme(default_scheme)}://{raw}"


def _pop_proxy_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Remove the flat proxy fields from "data", returning them by internal name."""
    found: dict[str, Any] = {}
    for key in [key for key in data if str(key).lower() in _PROXY_FIELDS]:
        found[_PROXY_FIELDS[str(key).lower()]] = data.pop(key)
    return found


def extract_proxy(data: dict[str, Any]) -> str | None:
    """Fold the proxy part of a task object into data["proxy"].

    An explicit proxy string wins over the flat fields; the flat fields are
    removed either way so credentials never ride into the stored payload twice.
    """
    fields = _pop_proxy_fields(data)
    explicit = _text(data.get("proxy"))
    if explicit is not None:
        url = normalize_proxy_string(explicit, default_scheme=_text(fields.get("proxy_type")))
        data["proxy"] = url
        return url
    data.pop("proxy", None)
    url = build_proxy_url(
        proxy_type=fields.get("proxy_type"),
        address=fields.get("proxy_address"),
        port=fields.get("proxy_port"),
        login=fields.get("proxy_login"),
        password=fields.get("proxy_password"),
    )
    if url is not None:
        data["proxy"] = url
    return url


# ---------------------------------------------------------------------------
# hCaptcha extras
# ---------------------------------------------------------------------------

_TRUE_FLAGS = frozenset({"1", "true", "yes", "on"})
_FALSE_FLAGS = frozenset({"0", "false", "no", "off", ""})


def _flag(raw: Any) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    text = str(raw).strip().lower()
    if text in _TRUE_FLAGS:
        return True
    if text in _FALSE_FLAGS:
        return False
    return None


def normalize_cookies(raw: Any) -> Any:
    """Canonicalise cookies to a list of {"name": ..., "value": ...} objects.

    Three shapes arrive in practice: 2captcha's "a=b; c=d" header string,
    capsolver's list of objects, and a JSON string carrying either. Anything we
    cannot parse is passed through untouched rather than dropped.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text[0] in "[{":
            try:
                return normalize_cookies(json.loads(text))
            except ValueError:
                return raw
        pairs = []
        for chunk in text.split(";"):
            name, separator, value = chunk.partition("=")
            if separator and name.strip():
                pairs.append({"name": name.strip(), "value": value.strip()})
        return pairs or raw
    if isinstance(raw, Mapping):
        return [{"name": str(name), "value": str(value)} for name, value in raw.items()]
    if isinstance(raw, (list, tuple)):
        items = []
        for entry in raw:
            if isinstance(entry, Mapping) and "name" in entry:
                items.append({"name": str(entry["name"]), "value": str(entry.get("value", ""))})
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                items.append({"name": str(entry[0]), "value": str(entry[1])})
            else:
                return raw
        return items
    return raw


def _enterprise_payload(raw: Any) -> dict[str, Any] | None:
    """enterprisePayload is documented as an object but sometimes arrives as JSON text."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                return None
            return parsed if isinstance(parsed, Mapping) else None
    return None


def normalize_hcaptcha_fields(data: dict[str, Any]) -> None:
    """Write the hCaptcha extras under their internal names.

    Canonical keys: invisible (bool), rqdata (str), enterprise_payload
    (object), cookies (list of name/value objects); host and userAgent already
    keep their names (user_agent is a declared task field).
    """
    raw_invisible = data.get("isInvisible")
    if raw_invisible is None:
        raw_invisible = data.get("invisible")
    flag = _flag(raw_invisible)
    if flag is not None:
        data["invisible"] = flag
    data.pop("isInvisible", None)

    raw_payload = data.pop("enterprisePayload", None)
    payload = _enterprise_payload(raw_payload)
    if payload is not None:
        data["enterprise_payload"] = payload
        rqdata = _text(payload.get("rqdata"))
        if rqdata is not None:
            data.setdefault("rqdata", rqdata)
    elif isinstance(raw_payload, str) and raw_payload.strip():
        # A bare token in enterprisePayload is rqdata, not JSON.
        data.setdefault("rqdata", raw_payload.strip())

    rqdata = _text(data.get("rqdata"))
    if rqdata is not None:
        data["rqdata"] = rqdata

    if data.get("cookies") is not None:
        data["cookies"] = normalize_cookies(data["cookies"])


# ---------------------------------------------------------------------------
# entry point used by the pydantic schema
# ---------------------------------------------------------------------------


def normalize_task_payload(data: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the wire -> internal mapping to one task object.

    - type: official name or snake_case -> CaptchaType;
    - proxy fields folded into one canonical proxy URL and removed;
    - hCaptcha extras renamed; everything else is left for pydantic's
      extra="allow" to carry into the per-type payload.

    Raises UnknownCaptchaType / ProxyFormatError (both ValueError) so the
    schema layer answers ERROR_BAD_REQUEST without a charge.
    """
    payload = dict(data)
    if "type" in payload:
        payload["type"] = resolve_captcha_type(payload["type"])
    extract_proxy(payload)
    normalize_hcaptcha_fields(payload)
    return payload
