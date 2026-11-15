"""BotGuard driver: the shim and the snapshot call run in a real JS runtime.

No network and no live challenge: the "interpreter" is a stub that answers the
way the real VM does -- vm.a(program, ...) hands back the snapshot function and
the snapshot calls back with the opaque response. That is exactly the part a
fake runtime cannot check: the shim has to satisfy the code probing it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import ChallengeExpiredError, UnsolvableError
from captcha.engines.base import BuildContext
from captcha.engines.botguard.builder import BotGuardBuilder

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

WAA_VM = """
var botguard = {
  a: function (program, callback) {
    return [function snapshot(fn) { fn("TOKEN-FOR-" + program); }];
  }
};
"""

BG_CTOR = """
var botguard = {
  bg: function (program) {
    return [function (a, b) { return "BG-" + program; }];
  }
};
"""


def make_ctx(interpreter: str, **extra: Any) -> BuildContext:
    return BuildContext(
        engine=AntibotEngine.BOTGUARD,
        site_url="https://www.google.com/recaptcha/api2/bframe",
        fingerprint=None,
        behavior=None,
        challenge_js=interpreter,
        timestamp_ms=1_700_000_000_000,
        cookies={"a": "1", "b": "2"},
        extra={"program": "PROG-1", "ua": UA, **extra},
    )


async def run_plan(plan: dict[str, Any]) -> Any:
    pytest.importorskip("py_mini_racer")
    from captcha.adapters.jssandbox.miniracer import LocalRuntime

    runtime = LocalRuntime(timeout_ms=5_000)
    try:
        return await runtime.exec_script(plan["script"], plan["globals"], timeout_ms=5_000)
    finally:
        await runtime.close()


async def test_the_vm_a_shape_produces_a_token() -> None:
    plan = await BotGuardBuilder().build(make_ctx(WAA_VM))

    raw = json.loads(await run_plan(plan))

    assert raw["token"] == "TOKEN-FOR-PROG-1"
    assert raw["error"] is None
    assert raw["stage"] == "snapshot"


async def test_the_bg_constructor_shape_produces_a_token() -> None:
    plan = await BotGuardBuilder().build(make_ctx(BG_CTOR))

    raw = json.loads(await run_plan(plan))

    assert raw["token"] == "BG-PROG-1"
    assert raw["error"] is None


async def test_a_payload_global_name_selects_the_vm() -> None:
    interpreter = "var myvm = { a: function (program, cb) { return [function (fn) { fn('VM-' + program); }]; } };"
    plan = await BotGuardBuilder().build(make_ctx(interpreter, global_name="myvm"))

    raw = json.loads(await run_plan(plan))

    assert raw["token"] == "VM-PROG-1"
    assert plan["global_name"] == "myvm"


async def test_a_missing_vm_is_reported_not_raised() -> None:
    plan = await BotGuardBuilder().build(make_ctx("var somethingElse = 1;"))

    raw = json.loads(await run_plan(plan))

    assert raw["error"] == "vm-missing"
    assert raw["stage"] == "lookup"
    assert raw["token"] is None


async def test_globals_carry_site_cookies_and_timestamp() -> None:
    plan = await BotGuardBuilder().build(make_ctx(WAA_VM))

    assert plan["globals"]["BG_PROGRAM"] == "PROG-1"
    assert plan["globals"]["BG_SITE"] == "https://www.google.com/recaptcha/api2/bframe"
    assert plan["globals"]["BG_HOST"] == "www.google.com"
    assert plan["globals"]["BG_COOKIES"] == "a=1; b=2"
    assert plan["globals"]["BG_TIMESTAMP"] == 1_700_000_000_000
    assert plan["globals"]["BG_UA"] == UA


async def test_the_builder_refuses_missing_halves() -> None:
    with pytest.raises(ChallengeExpiredError):
        await BotGuardBuilder().build(make_ctx(WAA_VM, program=""))
    with pytest.raises(UnsolvableError):
        await BotGuardBuilder().build(make_ctx(""))
