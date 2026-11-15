"""Constants shared by the integration suite.

A plain module (not a conftest import) so the test modules and the fixtures use
the same key and payload builder without depending on pytest's import order.
"""

from __future__ import annotations

from typing import Any

from captcha.domain.enums import CaptchaType

#: 2captcha clients send the key in the body; any 8..64 char value is valid.
CLIENT_KEY = "integration-key-0001"

#: Second account, used by the cross-account isolation test.
OTHER_CLIENT_KEY = "integration-key-0002"


def create_payload(**task_overrides: Any) -> dict[str, Any]:
    """A minimal valid createTask body for a solver-independent type.

    `text_captcha` lives in the cv lane, but no worker is needed: the test
    drains the stream itself, which is exactly how the gateway is supposed to be
    verified without a live captcha vendor in the loop.
    """
    task: dict[str, Any] = {
        "type": CaptchaType.TEXT.value,
        "websiteURL": "https://example.com/login",
        "websiteKey": "site-key",
    }
    task.update(task_overrides)
    return {"clientKey": CLIENT_KEY, "task": task}
