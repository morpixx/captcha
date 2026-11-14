"""Named JA3/JA4 profiles that curl_cffi can impersonate.

The `impersonate` string is what curl_cffi.requests.AsyncSession takes directly.
Keep this table in sync with curl_cffi's supported builds — mismatched strings
silently fall back to the lib's default, and the fingerprint the server reads
won't be what you asked for."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JA3Profile:
    name: str
    impersonate: str
    http2: bool = True
    alpn: tuple[str, ...] = ("h2", "http/1.1")


JA_PROFILES: dict[str, JA3Profile] = {
    "chrome124": JA3Profile("chrome124", "chrome124"),
    "chrome120": JA3Profile("chrome120", "chrome120"),
    "chrome116": JA3Profile("chrome116", "chrome116"),
    "chrome110": JA3Profile("chrome110", "chrome110"),
    "chrome107": JA3Profile("chrome107", "chrome107"),
    "firefox133": JA3Profile("firefox133", "firefox133"),
    "firefox120": JA3Profile("firefox120", "firefox120"),
    "safari17_0": JA3Profile("safari17_0", "safari17_0", alpn=("h2", "http/1.1")),
    "safari18_0": JA3Profile("safari18_0", "safari18_0", alpn=("h2", "http/1.1")),
    "edge127": JA3Profile("edge127", "edge127"),
    "chrome124_android": JA3Profile("chrome124_android", "chrome124_android"),
    "safari17_ios": JA3Profile("safari17_ios", "safari17_ios"),
}

_DEFAULT_DESKTOP = "chrome124"
_DEFAULT_MOBILE = "chrome124_android"


def pick_impersonate(hint: str, mobile: bool = False) -> str:
    """Fuzzy-match a hint ("chrome", "safari 17", "ff", "mobile chrome") to a
    curl_cffi impersonate string. Unknown hints fall back to current Chrome.

    Matching is deliberately loose — this runs on worker config that drifts."""
    default = _DEFAULT_MOBILE if mobile else _DEFAULT_DESKTOP
    if not hint:
        return default

    h = hint.strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if not h:
        return default

    # exact / alias match first
    if h in JA_PROFILES:
        return JA_PROFILES[h].impersonate

    # family aliases
    family = h
    for alias, canon in (("ff", "firefox"), ("googlechrome", "chrome"), ("msedge", "edge")):
        if family.startswith(alias):
            family = canon + family[len(alias):]
            break

    wants_mobile = mobile or any(tok in h for tok in ("android", "ios", "mobile"))

    # pick candidates matching the browser family
    candidates: list[JA3Profile] = []
    for key, prof in JA_PROFILES.items():
        k = key.lower()
        if family.startswith("chrome") and k.startswith("chrome"):
            candidates.append(prof)
        elif family.startswith("firefox") and k.startswith("firefox"):
            candidates.append(prof)
        elif family.startswith("safari") and k.startswith("safari"):
            candidates.append(prof)
        elif family.startswith("edge") and k.startswith("edge"):
            candidates.append(prof)

    if not candidates:
        return default

    # filter by mobile/desktop
    def is_mobile(p: JA3Profile) -> bool:
        return "android" in p.name or "ios" in p.name

    bucket = [p for p in candidates if is_mobile(p) == wants_mobile]
    if not bucket:
        bucket = candidates

    # prefer the highest numeric version suffix present in the hint,
    # else newest in the bucket (lexicographic descending on name).
    wanted_ver = _extract_version(h)
    if wanted_ver is not None:
        best = min(bucket, key=lambda p: abs((_extract_version(p.name) or 0) - wanted_ver))
        return best.impersonate

    bucket.sort(key=lambda p: (_extract_version(p.name) or 0), reverse=True)
    return bucket[0].impersonate


def _extract_version(s: str) -> int | None:
    """Pull leading integer version from a token like 'chrome124' or '17_0'."""
    digits = ""
    for ch in s:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None
