"""Single source of truth for per-captcha-type metadata.

Collapses three previously-independent lists — `pricing.COST_TABLE`,
`lanes._CV_TYPES`/`_PROTOCOL_TYPES`, and the family frozensets inside
`solution.py` — into one row per `CaptchaType`. A new type is now one entry
here instead of edits in four places; forgetting to update it is a boot error,
not a silent runtime surprise.

The old modules (`pricing`, `lanes`, `solution`) keep their public API and read
from this table, so call sites outside the domain are unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

import msgspec

from captcha.domain.enums import CaptchaType

Lane = Literal["http", "cv", "protocol", "browser"]

LANES: tuple[Lane, ...] = ("http", "cv", "protocol", "browser")


class SolutionKind(StrEnum):
    """How a `SolverResult` projects onto the 2captcha-shaped solution dict.

    One kind per branch in `solution.build_solution`. The projection logic
    still lives there — this enum is just how a type declares its shape.
    """

    TEXT = "text"
    RECAPTCHA = "recaptcha"
    HCAPTCHA = "hcaptcha"
    TURNSTILE = "turnstile"
    GEETEST_V3 = "geetest_v3"
    GEETEST_V4 = "geetest_v4"
    CLOUDFLARE = "cloudflare"
    COOKIE_ENGINE = "cookie_engine"
    AWSWAF = "awswaf"
    TOKEN = "token"
    FRIENDLY = "friendly"
    ALTCHA = "altcha"
    DATA_NEST = "data_nest"
    GRID = "grid"
    COORDS = "coords"
    BBOX = "bbox"
    ROTATE = "rotate"
    SLIDER = "slider"
    HOPSCOTCH = "hopscotch"


class TypeSpec(msgspec.Struct, frozen=True, kw_only=True):
    """One row per `CaptchaType`."""

    type: CaptchaType
    lane: Lane
    price: Decimal
    solution_kind: SolutionKind


def _row(ct: CaptchaType, lane: Lane, price: str, kind: SolutionKind) -> TypeSpec:
    return TypeSpec(type=ct, lane=lane, price=Decimal(price), solution_kind=kind)


# Order is grouped by family for readability only; the dict is lookup-only.
_ROWS: tuple[TypeSpec, ...] = (
    # proof-of-work / client verifiers
    _row(CaptchaType.FRIENDLY, "protocol", "0.00030", SolutionKind.FRIENDLY),
    _row(CaptchaType.ALTCHA, "protocol", "0.00030", SolutionKind.ALTCHA),
    # OCR / audio
    _row(CaptchaType.TEXT, "cv", "0.00050", SolutionKind.TEXT),
    _row(CaptchaType.MATH_IMAGE, "cv", "0.00050", SolutionKind.TEXT),
    _row(CaptchaType.IMAGE_TO_TEXT, "cv", "0.00050", SolutionKind.TEXT),
    _row(CaptchaType.AUDIO, "cv", "0.00070", SolutionKind.TEXT),
    # image geometry / grid
    _row(CaptchaType.ROTATE, "cv", "0.00080", SolutionKind.ROTATE),
    _row(CaptchaType.SLIDER, "cv", "0.00080", SolutionKind.SLIDER),
    _row(CaptchaType.CLICK, "cv", "0.00100", SolutionKind.COORDS),
    _row(CaptchaType.COORDINATES, "cv", "0.00100", SolutionKind.COORDS),
    _row(CaptchaType.BOUNDING_BOX, "cv", "0.00120", SolutionKind.BBOX),
    _row(CaptchaType.CLASSIFICATION_GRID, "cv", "0.00100", SolutionKind.GRID),
    _row(CaptchaType.DRAW_AROUND, "cv", "0.00120", SolutionKind.COORDS),
    _row(CaptchaType.DRAG_DROP, "cv", "0.00120", SolutionKind.COORDS),
    # Custom order-independent card choice (not one of the 62 vendor types).
    _row(CaptchaType.HOPSCOTCH, "cv", "0.00200", SolutionKind.HOPSCOTCH),
    # --- lane rule -------------------------------------------------------
    # "cv" means the worker needs the ML stack (torch / opencv / whisper) or
    # delegates to a solver that does. A type whose solver calls _solve_cv() is
    # cv by definition: the delegated slider/click/rotate solver must be
    # importable in the same process, and that only holds where --extra cv is.
    # --- hCaptcha
    _row(CaptchaType.HCAPTCHA, "cv", "0.00150", SolutionKind.HCAPTCHA),
    _row(CaptchaType.HCAPTCHA_ENTERPRISE, "cv", "0.00300", SolutionKind.HCAPTCHA),
    # reCAPTCHA
    _row(CaptchaType.RECAPTCHA_V2, "cv", "0.00200", SolutionKind.RECAPTCHA),
    _row(CaptchaType.RECAPTCHA_V2_ENTERPRISE, "cv", "0.00300", SolutionKind.RECAPTCHA),
    _row(CaptchaType.RECAPTCHA_V2_INVISIBLE, "cv", "0.00200", SolutionKind.RECAPTCHA),
    _row(CaptchaType.RECAPTCHA_V2_CALLBACK, "cv", "0.00200", SolutionKind.RECAPTCHA),
    _row(CaptchaType.RECAPTCHA_V3, "http", "0.00200", SolutionKind.RECAPTCHA),
    _row(CaptchaType.RECAPTCHA_V3_ENTERPRISE, "http", "0.00300", SolutionKind.RECAPTCHA),
    # GeeTest
    _row(CaptchaType.GEETEST_V3, "cv", "0.00180", SolutionKind.GEETEST_V3),
    _row(CaptchaType.GEETEST_V4, "cv", "0.00200", SolutionKind.GEETEST_V4),
    # FunCaptcha / Arkose
    _row(CaptchaType.FUNCAPTCHA, "cv", "0.00180", SolutionKind.TOKEN),
    _row(CaptchaType.FUNCAPTCHA_BDA, "http", "0.00200", SolutionKind.TOKEN),
    _row(CaptchaType.FUNCAPTCHA_ENTERPRISE, "cv", "0.00300", SolutionKind.TOKEN),
    # Turnstile
    _row(CaptchaType.TURNSTILE, "http", "0.00150", SolutionKind.TURNSTILE),
    _row(CaptchaType.TURNSTILE_INVISIBLE, "http", "0.00150", SolutionKind.TURNSTILE),
    # Cloudflare challenge page
    _row(CaptchaType.CF_CHALLENGE, "http", "0.00300", SolutionKind.CLOUDFLARE),
    # cookie-emitting antibot engines
    _row(CaptchaType.AKAMAI_SENSOR, "http", "0.00400", SolutionKind.COOKIE_ENGINE),
    _row(CaptchaType.AKAMAI_SBSD, "http", "0.00500", SolutionKind.COOKIE_ENGINE),
    _row(CaptchaType.DATADOME_SLIDER, "http", "0.00300", SolutionKind.COOKIE_ENGINE),
    _row(CaptchaType.DATADOME_FULL, "http", "0.00400", SolutionKind.COOKIE_ENGINE),
    _row(CaptchaType.KASADA, "http", "0.00400", SolutionKind.COOKIE_ENGINE),
    _row(CaptchaType.PERIMETERX, "http", "0.00400", SolutionKind.COOKIE_ENGINE),
    _row(CaptchaType.IMPERVA, "http", "0.00400", SolutionKind.COOKIE_ENGINE),
    _row(CaptchaType.TSPD, "http", "0.00300", SolutionKind.COOKIE_ENGINE),
    _row(CaptchaType.TEMU, "http", "0.00250", SolutionKind.COOKIE_ENGINE),
    # nested-`data` vendors
    _row(CaptchaType.CASTLE, "http", "0.00300", SolutionKind.DATA_NEST),
    _row(CaptchaType.HUNT, "cv", "0.00200", SolutionKind.DATA_NEST),
    _row(CaptchaType.BASILISK, "cv", "0.00200", SolutionKind.DATA_NEST),
    _row(CaptchaType.ALIBABA, "cv", "0.00300", SolutionKind.DATA_NEST),
    _row(CaptchaType.TENCENT, "cv", "0.00150", SolutionKind.DATA_NEST),
    # AWS WAF — token/cookie hybrid
    _row(CaptchaType.AWSWAF, "cv", "0.00300", SolutionKind.AWSWAF),
    # token-grade engines (long tail)
    _row(CaptchaType.CYBERSIARA, "http", "0.00180", SolutionKind.TOKEN),
    _row(CaptchaType.TWITCH_INTEGRITY, "http", "0.00300", SolutionKind.TOKEN),
    _row(CaptchaType.BOTGUARD, "http", "0.00300", SolutionKind.TOKEN),
    _row(CaptchaType.BOTDEFLECTOR, "http", "0.00300", SolutionKind.TOKEN),
    # Shein — classification solver that returns click points
    _row(CaptchaType.SHEIN, "cv", "0.00300", SolutionKind.COORDS),
    # interactive token vendors
    _row(CaptchaType.MTCAPTCHA, "cv", "0.00150", SolutionKind.TOKEN),
    _row(CaptchaType.YIDUN, "cv", "0.00180", SolutionKind.TOKEN),
    _row(CaptchaType.PROSOPO, "cv", "0.00100", SolutionKind.TOKEN),
    _row(CaptchaType.CAPTCHAFOX, "cv", "0.00180", SolutionKind.TOKEN),
    _row(CaptchaType.VK, "cv", "0.00150", SolutionKind.TOKEN),
    _row(CaptchaType.YANDEX, "cv", "0.00150", SolutionKind.TOKEN),
    _row(CaptchaType.BINANCE, "cv", "0.00300", SolutionKind.TOKEN),
    _row(CaptchaType.CUTCAPTCHA, "cv", "0.00150", SolutionKind.TOKEN),
    _row(CaptchaType.CAPY, "cv", "0.00180", SolutionKind.TOKEN),
    _row(CaptchaType.KEYCAPTCHA, "cv", "0.00150", SolutionKind.TOKEN),
    _row(CaptchaType.LEMIN, "cv", "0.00150", SolutionKind.TOKEN),
    _row(CaptchaType.ATBCAPTCHA, "cv", "0.00150", SolutionKind.TOKEN),
)

CATALOG: dict[CaptchaType, TypeSpec] = {row.type: row for row in _ROWS}


def _validate_catalog() -> None:
    """Every `CaptchaType` must have a row; duplicate rows are a bug.

    A new enum member without a catalog entry is a boot-time RuntimeError so it
    cannot ship silently. Duplicate rows would otherwise be flattened by the
    dict comprehension above, so check against `_ROWS` directly.
    """
    seen: set[CaptchaType] = set()
    duplicates: set[CaptchaType] = set()
    for row in _ROWS:
        if row.type in seen:
            duplicates.add(row.type)
        seen.add(row.type)
    if duplicates:
        names = ", ".join(sorted(d.value for d in duplicates))
        raise RuntimeError(f"captcha.domain.catalog: duplicate TypeSpec for: {names}")

    missing = set(CaptchaType) - set(CATALOG)
    if missing:
        names = ", ".join(sorted(m.value for m in missing))
        raise RuntimeError(f"captcha.domain.catalog: missing TypeSpec for: {names}")


_validate_catalog()


DEFAULT_PRICE: Decimal = Decimal("0.00100")


def spec(ct: CaptchaType) -> TypeSpec:
    """Return the `TypeSpec` for a known type. Raises `KeyError` otherwise.

    `_validate_catalog()` at import keeps the catalog in sync with the enum, so
    a `KeyError` here can only fire on a value that is not a `CaptchaType`
    member (e.g. an attacker-crafted string that bypassed validation upstream).
    """
    return CATALOG[ct]


def price(ct: CaptchaType) -> Decimal:
    """Price for a type; conservative default for a type not yet in the catalog."""
    row = CATALOG.get(ct)
    return row.price if row is not None else DEFAULT_PRICE


def lane_for(ct: CaptchaType) -> Lane:
    """Lane for a type; falls back to `http` for types not in the catalog."""
    row = CATALOG.get(ct)
    return row.lane if row is not None else "http"


def solution_kind(ct: CaptchaType) -> SolutionKind | None:
    """Solution kind for a type; `None` means "unmapped" (caller emits a generic shape)."""
    row = CATALOG.get(ct)
    return row.solution_kind if row is not None else None


def types_by_lane(lane: Lane) -> frozenset[CaptchaType]:
    return frozenset(t for t, s in CATALOG.items() if s.lane == lane)


def types_by_solution_kind(kind: SolutionKind) -> frozenset[CaptchaType]:
    return frozenset(t for t, s in CATALOG.items() if s.solution_kind == kind)


def all_specs() -> tuple[TypeSpec, ...]:
    """Every row, in insertion order. Useful for admin dumps and docs."""
    return _ROWS


__all__ = [
    "CATALOG",
    "DEFAULT_PRICE",
    "LANES",
    "Lane",
    "SolutionKind",
    "TypeSpec",
    "all_specs",
    "lane_for",
    "price",
    "solution_kind",
    "spec",
    "types_by_lane",
    "types_by_solution_kind",
]
