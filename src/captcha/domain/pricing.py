"""Per-type estimated cost.

Reads the single `catalog.CATALOG` table. To change a price or add a type,
edit `captcha.domain.catalog`, not here.
"""

from __future__ import annotations

from decimal import Decimal

from captcha.domain.catalog import DEFAULT_PRICE, price
from captcha.domain.enums import CaptchaType

# Historical re-export: callers used to read this constant directly.
DEFAULT_COST: Decimal = DEFAULT_PRICE


def estimate_cost(ct: CaptchaType) -> Decimal:
    """Up-front charge for creating a task of the given type."""
    return price(ct)


__all__ = ["DEFAULT_COST", "estimate_cost"]
