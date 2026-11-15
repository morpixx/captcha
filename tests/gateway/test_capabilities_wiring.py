from __future__ import annotations

from captcha.domain.enums import CaptchaType
from captcha.services.gateway.app import _available_types


def test_browser_lane_type_needs_live_heartbeat() -> None:
    registry = frozenset({CaptchaType.HCAPTCHA, CaptchaType.FRIENDLY})
    assert _available_types(registry, frozenset()) == {CaptchaType.FRIENDLY}


def test_live_browser_type_is_sold() -> None:
    registry = frozenset({CaptchaType.HCAPTCHA, CaptchaType.FRIENDLY})
    assert _available_types(registry, frozenset({CaptchaType.HCAPTCHA})) == {
        CaptchaType.FRIENDLY,
        CaptchaType.HCAPTCHA,
    }
