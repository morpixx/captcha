"""Per-type estimated cost. Used by createTask to charge the account up front.

Numbers are in USD and deliberately close to 2captcha's published rates so a
client can migrate without rewriting their billing. Over/under-charge is
corrected by the worker on terminal status (refund on failure).
"""
from __future__ import annotations

from decimal import Decimal

from captcha.domain.enums import CaptchaType

# Default when a type isn't on the table yet.
DEFAULT_COST: Decimal = Decimal("0.00100")

COST_TABLE: dict[CaptchaType, Decimal] = {
    # proof-of-work / client verifiers — cheap, CPU-only
    CaptchaType.FRIENDLY: Decimal("0.00030"),
    CaptchaType.ALTCHA: Decimal("0.00030"),

    # OCR class
    CaptchaType.TEXT: Decimal("0.00050"),
    CaptchaType.IMAGE_TO_TEXT: Decimal("0.00050"),
    CaptchaType.MATH_IMAGE: Decimal("0.00050"),
    CaptchaType.AUDIO: Decimal("0.00070"),

    # grid / geometry
    CaptchaType.CLASSIFICATION_GRID: Decimal("0.00100"),
    CaptchaType.CLICK: Decimal("0.00100"),
    CaptchaType.COORDINATES: Decimal("0.00100"),
    CaptchaType.BOUNDING_BOX: Decimal("0.00120"),
    CaptchaType.DRAW_AROUND: Decimal("0.00120"),
    CaptchaType.DRAG_DROP: Decimal("0.00120"),
    CaptchaType.ROTATE: Decimal("0.00080"),
    CaptchaType.SLIDER: Decimal("0.00080"),

    # token-grade public captchas
    CaptchaType.RECAPTCHA_V2: Decimal("0.00200"),
    CaptchaType.RECAPTCHA_V2_ENTERPRISE: Decimal("0.00300"),
    CaptchaType.RECAPTCHA_V2_INVISIBLE: Decimal("0.00200"),
    CaptchaType.RECAPTCHA_V2_CALLBACK: Decimal("0.00200"),
    CaptchaType.RECAPTCHA_V3: Decimal("0.00200"),
    CaptchaType.RECAPTCHA_V3_ENTERPRISE: Decimal("0.00300"),
    CaptchaType.HCAPTCHA: Decimal("0.00150"),
    CaptchaType.HCAPTCHA_ENTERPRISE: Decimal("0.00300"),
    CaptchaType.TURNSTILE: Decimal("0.00150"),
    CaptchaType.TURNSTILE_INVISIBLE: Decimal("0.00150"),
    CaptchaType.GEETEST_V3: Decimal("0.00180"),
    CaptchaType.GEETEST_V4: Decimal("0.00200"),
    CaptchaType.FUNCAPTCHA: Decimal("0.00180"),
    CaptchaType.FUNCAPTCHA_BDA: Decimal("0.00200"),
    CaptchaType.FUNCAPTCHA_ENTERPRISE: Decimal("0.00300"),
    CaptchaType.MTCAPTCHA: Decimal("0.00150"),
    CaptchaType.TENCENT: Decimal("0.00150"),
    CaptchaType.YIDUN: Decimal("0.00180"),
    CaptchaType.PROSOPO: Decimal("0.00100"),
    CaptchaType.CAPTCHAFOX: Decimal("0.00180"),
    CaptchaType.CAPY: Decimal("0.00180"),
    CaptchaType.KEYCAPTCHA: Decimal("0.00150"),
    CaptchaType.LEMIN: Decimal("0.00150"),
    CaptchaType.ATBCAPTCHA: Decimal("0.00150"),
    CaptchaType.CUTCAPTCHA: Decimal("0.00150"),
    CaptchaType.CYBERSIARA: Decimal("0.00180"),

    # antibot engines — expensive, usually need a sensor + proxy
    CaptchaType.CF_CHALLENGE: Decimal("0.00300"),
    CaptchaType.DATADOME_SLIDER: Decimal("0.00300"),
    CaptchaType.DATADOME_FULL: Decimal("0.00400"),
    CaptchaType.AKAMAI_SENSOR: Decimal("0.00400"),
    CaptchaType.AKAMAI_SBSD: Decimal("0.00500"),
    CaptchaType.KASADA: Decimal("0.00400"),
    CaptchaType.PERIMETERX: Decimal("0.00400"),
    CaptchaType.IMPERVA: Decimal("0.00400"),
    CaptchaType.CASTLE: Decimal("0.00300"),
    CaptchaType.TSPD: Decimal("0.00300"),
    CaptchaType.AWSWAF: Decimal("0.00300"),
    CaptchaType.BOTGUARD: Decimal("0.00300"),
    CaptchaType.BOTDEFLECTOR: Decimal("0.00300"),
    CaptchaType.TWITCH_INTEGRITY: Decimal("0.00300"),
    CaptchaType.SHEIN: Decimal("0.00300"),
    CaptchaType.ALIBABA: Decimal("0.00300"),
    CaptchaType.VK: Decimal("0.00150"),
    CaptchaType.YANDEX: Decimal("0.00150"),
    CaptchaType.TEMU: Decimal("0.00250"),
    CaptchaType.BINANCE: Decimal("0.00300"),
    CaptchaType.HUNT: Decimal("0.00200"),
    CaptchaType.BASILISK: Decimal("0.00200"),
}


def estimate_cost(ct: CaptchaType) -> Decimal:
    return COST_TABLE.get(ct, DEFAULT_COST)
