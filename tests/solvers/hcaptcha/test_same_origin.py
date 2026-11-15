from __future__ import annotations

import pytest

from captcha.domain import CaptchaType, Task
from captcha.domain.errors import UnsolvableError
from captcha.domain.task import new_task_id
from captcha.solvers.hcaptcha import wire


def test_same_origin_is_required() -> None:
    task = Task(
        id=new_task_id(),
        type=CaptchaType.HCAPTCHA,
        sitekey="b3c619fc-4b72-4838-9bf2-398888588d62",
        payload={"host": "account.riotgames.com"},
        site_url="https://store.steampowered.com/login",
    )
    with pytest.raises(UnsolvableError, match="same origin"):
        wire.params_from_task(task)


def test_same_origin_is_accepted() -> None:
    task = Task(
        id=new_task_id(),
        type=CaptchaType.HCAPTCHA,
        sitekey="b3c619fc-4b72-4838-9bf2-398888588d62",
        payload={"host": "account.riotgames.com"},
        site_url="https://account.riotgames.com/",
    )
    params = wire.params_from_task(task)
    assert params.host == "account.riotgames.com"
    assert params.href.startswith("https://account.riotgames.com/")
