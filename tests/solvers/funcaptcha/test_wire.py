from __future__ import annotations

import json

import pytest
from helpers import CHALLENGE_TOKEN, PAGE_URL, SITEKEY, SURL, TOKEN, FakeHTTP, FakeResponse, fixture, make_task

from captcha.domain.errors import SensorError
from captcha.solvers import SolverContext
from captcha.solvers.funcaptcha import wire


def cfg() -> object:
    from captcha.solvers.funcaptcha import settings

    return settings.ArkoseSettings(
        surl=SURL,
        public_key=SITEKEY,
        capi_version="4.4.5",
        build_id="3f8a1c92-1a2b-4c3d-8e4f-0123456789ab",
        enforcement_hash="2d1c8a89671586563cb793ae2399b954",
        rsa_public_key="MIIB",
    )


# --- encoding ----------------------------------------------------------------


def test_form_encode_keeps_order_and_quotes_like_the_widget():
    body = wire.form_encode([("bda", "a+b/c d:e"), ("public_key", SITEKEY)])

    assert body.startswith("bda=a%2Bb/c%20d%3Ae&public_key=")
    assert " " not in body


def test_esync_value_is_quantised_to_six_hours():
    assert wire.esync_value(1_712_000_000) == str(79259 * 21600)


def test_split_token_reads_region_and_suppression():
    parts = wire.split_token(TOKEN)

    assert parts.token == "98718c6b2e1d4f8a9b3c5d7e"
    assert parts.region == "eu-west-1"
    assert parts.suppressed is True
    assert parts.segments[0] == "98718c6b2e1d4f8a9b3c5d7e"


def test_split_token_prefers_an_explicit_sid_segment():
    parts = wire.split_token(CHALLENGE_TOKEN)

    assert parts.sid == "us-west-2"
    assert parts.suppressed is False


def test_split_token_falls_back_to_the_region_for_sid():
    parts = wire.split_token(TOKEN)

    assert parts.sid == "eu-west-1"


def test_split_token_rejects_empty():
    with pytest.raises(SensorError):
        wire.split_token("")


# --- token endpoint ----------------------------------------------------------


async def test_open_session_sends_the_widget_form_and_headers():
    http = FakeHTTP(FakeResponse({"token": TOKEN}))
    task = make_task()

    opened = await wire.open_session(
        SolverContext(http=http),
        cfg(),
        task,
        bda_value="sealed-blob",
        bda_field="c",
        user_agent="UA/1",
        blob="blob-value",
    )

    assert opened["token"] == TOKEN
    call = http.call(0)
    assert call["url"] == f"{SURL}/fc/gt2/public_key/{SITEKEY}"
    body = call["data"]
    assert body.startswith("c=sealed-blob&public_key=")
    assert "&data%5Bblob%5D=blob-value" in body
    assert "&capi_version=4.4.5" in body
    assert "&language=en-US" in body
    headers = call["headers"]
    assert headers["origin"] == "https://www.example.com"
    assert headers["referer"] == PAGE_URL + "/"
    assert headers["ark-build-id"] == "3f8a1c92-1a2b-4c3d-8e4f-0123456789ab"
    assert headers["x-ark-esync-value"].isdigit()


async def test_open_session_unwraps_a_json_body_wrapper():
    wrapped = {"body": json.dumps({"token": TOKEN})}
    http = FakeHTTP(FakeResponse(wrapped))

    opened = await wire.open_session(
        SolverContext(http=http), cfg(), make_task(), bda_value="x", bda_field="bda"
    )

    assert opened["token"] == TOKEN


async def test_open_session_non_200_is_sensor_error():
    http = FakeHTTP(FakeResponse(b"nope", status=403))

    with pytest.raises(SensorError) as exc:
        await wire.open_session(SolverContext(http=http), cfg(), make_task(), bda_value="x", bda_field="c")

    assert exc.value.context["status"] == 403


async def test_open_session_non_json_is_sensor_error():
    http = FakeHTTP(FakeResponse(b"<html>", status=200))

    with pytest.raises(SensorError):
        await wire.open_session(SolverContext(http=http), cfg(), make_task(), bda_value="x", bda_field="c")


# --- challenge ---------------------------------------------------------------


async def test_fetch_challenge_re_sends_the_token_segments():
    http = FakeHTTP(FakeResponse(fixture("gfct_grid.json")))

    challenge = await wire.fetch_challenge(SolverContext(http=http), cfg(), make_task(), CHALLENGE_TOKEN, user_agent="UA/1")

    assert challenge["challengeID"] == "713184db7d62a8982.4535671902"
    call = http.call(0)
    assert call["url"] == f"{SURL}/fc/gfct/"
    assert call["params"] == {
        "token": "98718c6b2e1d4f8a9b3c5d7e",
        "sid": "us-west-2",
        "render_type": "canvas",
        "analytics_iter": "40",
        "r": "eu-west-1",
    }
    assert call["headers"]["user-agent"] == "UA/1"


# --- answer ------------------------------------------------------------------


async def test_submit_answer_sends_session_game_and_guess():
    http = FakeHTTP(FakeResponse(fixture("ca_answered.json")))

    reply = await wire.submit_answer(
        SolverContext(http=http),
        cfg(),
        make_task(payload={"bio": "bio-blob"}),
        session_token=CHALLENGE_TOKEN,
        game_token="713184db7d62a8982.4535671902",
        guess="[0,2]",
        bio="bio-blob",
        user_agent="UA/1",
    )

    assert reply["solved"] is True
    call = http.call(0)
    assert call["url"] == f"{SURL}/fc/ca/"
    assert call["data"].startswith("session_token=")
    assert "&game_token=713184db7d62a8982.4535671902" in call["data"]
    assert "&guess=%5B0%2C2%5D" in call["data"]
    assert "&bio=bio-blob" in call["data"]
    assert call["headers"]["x-requested-id"]
    assert call["headers"]["x-newrelic-timestamp"].isdigit()


async def test_submit_answer_appends_caller_supplied_form_fields():
    http = FakeHTTP(FakeResponse(fixture("ca_answered.json")))
    task = make_task(payload={"arkose_extra_form": {"tguess": "abc", "capi": "x"}})

    await wire.submit_answer(
        SolverContext(http=http),
        cfg(),
        task,
        session_token=CHALLENGE_TOKEN,
        game_token="g",
        guess="[]",
    )

    body = http.call(0)["data"]
    assert body.endswith("&tguess=abc&capi=x")


def test_solved_accepts_both_reply_shapes():
    assert wire.solved({"solved": True}) is True
    assert wire.solved({"answered": "answered"}) is True
    assert wire.solved({"solved": False}) is False
    assert wire.solved({}) is False


def test_next_round_images_collects_every_supported_key():
    reply = {"_challenge_imgs": ["a"], "challenge_imgs": ["b"], "_challenge_imgs_2": ["c", None]}

    assert wire.next_round_images(reply) == ["a", "b", "c"]
    assert wire.next_round_images({"solved": True}) == []


def test_blob_from_accepts_every_client_spelling():
    assert wire.blob_from({"data[blob]": "one"}) == "one"
    assert wire.blob_from({"blob": "two"}) == "two"
    assert wire.blob_from({"data": {"blob": "three"}}) == "three"
    assert wire.blob_from({}) == ""


def test_encode_guess_defaults_to_compact_json():
    assert wire.encode_guess([0, 2]) == "[0,2]"
    assert wire.encode_guess([90]) == "[90]"


def test_encode_guess_uses_the_codec_when_supplied():
    class Codec:
        def encode(self, answer, challenge):
            return "cipher:" + ",".join(str(item) for item in answer)

    ctx = SolverContext(extra={"arkose_guess_codec": Codec()})

    assert wire.encode_guess([0, 2], challenge={}, ctx=ctx) == "cipher:0,2"


def test_encode_guess_rejects_an_empty_codec_result():
    class Codec:
        def encode(self, answer, challenge):
            return ""

    with pytest.raises(SensorError):
        wire.encode_guess([0], challenge={}, ctx=SolverContext(extra={"arkose_guess_codec": Codec()}))
