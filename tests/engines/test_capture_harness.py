"""Replay tests for `captcha.engines.capture` -- the program script-driven engines run.

Castle, CyberSiARA, Imperva, PerimeterX (press bundle) and F5 Shape do not publish
a payload format: their script *is* the generator. The harness runs that script in
a browser-shaped realm and reports what it wrote, which is what the five builders
and their solvers rely on. These tests pin that contract:

* the seeded realm (location, UA, cookies, storage) is visible to the script,
* `document.cookie` / `localStorage` / XHR / `fetch` / `sendBeacon` land in the report,
* a script that throws still reports what it captured before the throw,
* `parse` classifies a non-capture answer instead of guessing.

No network, no browser: the runtime is the same py-mini-racer `LocalRuntime` a
worker uses, wrapped in an IIFE so the program's trailing `return` works.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from captcha.adapters.jssandbox import LocalRuntime
from captcha.domain.errors import SensorError
from captcha.engines import capture

PAGE_URL = "https://acme.example/login"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"


@pytest.fixture
async def runtime() -> Any:
    rt = LocalRuntime(timeout_ms=5000)
    try:
        yield rt
    finally:
        await rt.close()


async def run(runtime: Any, script: str, *, setup: str = "", engine: str = "probe") -> capture.Capture:
    program = capture.wrap(script, setup=setup, engine=engine)
    return capture.parse(await runtime.exec_script(program, {}, timeout_ms=5000), engine=engine)


async def test_program_seeds_every_field_the_script_can_read(runtime: Any) -> None:
    probe = r"""
localStorage.setItem("seen.href", location.href);
localStorage.setItem("seen.origin", location.origin);
localStorage.setItem("seen.ua", navigator.userAgent);
localStorage.setItem("seen.cookie", String(document.cookie));
localStorage.setItem("seen.seeded", String(localStorage.getItem("seeded")));
localStorage.setItem("seen.missing", String(localStorage.getItem("absent")));
"""
    cap = await run(
        runtime,
        probe,
        setup=capture.setup_js(
            href=PAGE_URL,
            user_agent=UA,
            cookies={"_pxvid": "visitor-1"},
            storage={"seeded": "s1"},
        ),
    )
    assert cap.storage["seen.href"] == PAGE_URL
    assert cap.storage["seen.origin"] == "https://acme.example"
    assert cap.storage["seen.ua"] == UA
    assert "_pxvid=visitor-1" in cap.storage["seen.cookie"]
    assert cap.storage["seen.seeded"] == "s1"
    # getItem on an absent key is null, not "undefined"
    assert cap.storage["seen.missing"] == "null"


async def test_script_that_throws_still_reports_what_it_wrote(runtime: Any) -> None:
    cap = await run(runtime, 'document.cookie = "TS01abcdef=deadbeef; path=/"; throw new Error("boom");')
    assert cap.cookies == {"TS01abcdef": "deadbeef"}
    assert cap.error_tail().startswith("Error: boom")


async def test_xhr_fetch_and_sendbeacon_are_all_captured(runtime: Any) -> None:
    probe = r"""
var xhr = new XMLHttpRequest();
xhr.open("post", "/_Incapsula_Resource?SWKMTFSR=1&e=abc");
xhr.setRequestHeader("content-type", "application/json");
xhr.send('{"solution":{"version":"stable"}}');
fetch("/api/v2/collector", {method: "POST", headers: {"x-px": "1"}, body: "payload=abc"});
navigator.sendBeacon("/beacon", "b=2");
"""
    cap = await run(runtime, probe)
    assert [request.method for request in cap.requests] == ["POST", "POST", "POST"]
    assert [request.url for request in cap.requests] == [
        "/_Incapsula_Resource?SWKMTFSR=1&e=abc",
        "/api/v2/collector",
        "/beacon",
    ]
    assert cap.requests[0].headers == {"content-type": "application/json"}
    assert json.loads(cap.requests[0].body or "{}") == {"solution": {"version": "stable"}}
    assert cap.request(needle="collector").body == "payload=abc"
    assert cap.request(method="post", needle="beacon").method == "POST"


async def test_storage_shim_behaves_like_the_browser_one(runtime: Any) -> None:
    probe = r"""
localStorage.setItem("n", 1);
localStorage.setItem("tmp", "x");
localStorage.removeItem("tmp");
var xhr = new XMLHttpRequest();
xhr.open("GET", "/probe");
xhr.send();
localStorage.setItem("coerced", localStorage.getItem("n"));
localStorage.setItem("cleared", String(localStorage.getItem("tmp")));
"""
    cap = await run(runtime, probe)
    assert cap.storage["coerced"] == "1"
    assert cap.storage["cleared"] == "null"
    assert "tmp" not in cap.storage
    assert cap.cookies == {}


async def test_crypto_shim_is_deterministic(runtime: Any) -> None:
    probe = r"""
var a = new Uint8Array(8);
crypto.getRandomValues(a);
localStorage.setItem("bytes", Array.prototype.join.call(a, ","));
localStorage.setItem("uuid", crypto.randomUUID());
"""
    first = await run(runtime, probe)
    second = await run(runtime, probe)
    assert first.storage == second.storage
    assert first.storage["uuid"] == "00000000-0000-4000-8000-000000000000"


def test_wrap_refuses_an_empty_script() -> None:
    with pytest.raises(SensorError):
        capture.wrap("")
    with pytest.raises(SensorError):
        capture.wrap("   \n  ", engine="imperva")


def test_setup_js_is_empty_when_there_is_nothing_to_seed() -> None:
    assert capture.setup_js() == ""
    seeded = capture.setup_js(href=PAGE_URL, user_agent=UA, cookies={"a": "b"}, storage={"c": "d"})
    assert 'location.href = "https://acme.example/login";' in seeded
    assert 'navigator.userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0";' in seeded
    assert 'document.cookie = "a=b; path=/";' in seeded
    assert 'localStorage.setItem("c", "d");' in seeded


@pytest.mark.parametrize("raw", [None, "not json", "[1,2]", 42, ""])
def test_parse_rejects_answers_that_are_not_a_capture(raw: Any) -> None:
    with pytest.raises(SensorError):
        capture.parse(raw, engine="probe")


def test_parse_accepts_an_already_decoded_capture() -> None:
    cap = capture.parse(
        {
            "cookies": {"TS01abcdef": "v"},
            "storage": {"__cuid": "c1"},
            "requests": [{"url": "https://acme.example/x", "method": "post", "body": "b"}],
            "errors": ["e"],
        },
        engine="probe",
    )
    assert cap.cookie("TS01abcdef") == "v"
    assert cap.stored("__cuid") == "c1"
    assert cap.requests[0].method == "POST"
    assert cap.requests[0].is_post
    assert cap.errors == ("e",)


def test_capture_helpers_pick_by_prefix_and_bucket() -> None:
    cap = capture.Capture(
        cookies={"TSPD_101": "one", "TS01abc": "two"},
        storage={"master_url_token": "m"},
        session={"master_url_token": "s"},
        requests=(capture.CapturedRequest(url="https://acme.example/x", method="POST", body=None),),
    )
    assert cap.cookie_with_prefix(("TSPD",)) == ("TSPD_101", "one")
    assert cap.cookie_with_prefix(("TS0",)) == ("TS01abc", "two")
    assert cap.cookie_with_prefix(("nope",)) == ("", "")
    assert cap.token("master_url_token") == "m"  # storage wins over session
    assert cap.stored("absent") == ""
    assert cap.request(needle="nowhere") is None


def test_capture_to_dict_is_json_serialisable() -> None:
    cap = capture.Capture(cookies={"a": "b"}, requests=(capture.CapturedRequest(url="u", method="POST"),))
    assert json.loads(json.dumps(cap.to_dict()))["requests"][0]["url"] == "u"
