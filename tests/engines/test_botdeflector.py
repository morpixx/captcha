"""BotDeflector engine layer: the Cuckoo-cycle PoW and the fingerprint header.

The PoW is the part that has a public cross-language reference, so it is pinned
against that vector rather than against our own output. The fingerprint half has
no reference at all -- it is a fingerprintjs-shaped blob -- so the test pins the
contract instead: deterministic bytes, UA required, client capture wins.
"""

from __future__ import annotations

import json

import pytest

from captcha.domain.enums import AntibotEngine
from captcha.domain.errors import SensorError
from captcha.engines.base import BuildContext
from captcha.engines.botdeflector import pow as pow_mod
from captcha.engines.botdeflector.builder import (
    DEFAULT_DOMAIN,
    HEADER_FINGERPRINT,
    QUEUE_IT_HOST,
    BotDeflectorBuilder,
)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

#: Public reference vector (glizzykingdreko/botdeflector-pow, MIT).
FIXTURE_PARAMS = {
    "NODES_BITS": 14,
    "EDGES_BITS": 16,
    "CYCLE_LENGTH": 8,
    "GRAPH_SEED": "botdeflector-pow-fixture-v1",
}
FIXTURE_CHALLENGES = [
    "1bbcad29320f20847a515cc854063cda0ddaea20cdcff45eb6b5e0e9ee15fbd4",
    "042896dc1966b8a6214e5383aba5b8b931cfa049d17aafa37eb8a77c859b95da",
    "dba0082ec68e72d47fa926dcc91fb9b00961ceb4342554a0c2aa567144a5a6d3",
]
FIXTURE_EDGES = [
    [1916, 9296, 32052, 28331, 59430, 48251, 26106, 54087],
    [3500, 3969, 21508, 12185, 13737, 20663, 32303, 44139],
    [5814, 6465, 4652, 3891, 38592, 50674, 1006, 28273],
]


def make_ctx(**extra: object) -> BuildContext:
    return BuildContext(
        engine=AntibotEngine.BOTDEFLECTOR,
        site_url="https://botdeflector.eu/challenge",
        fingerprint=None,
        behavior=None,
        extra={"ua": UA, **extra},
    )


# --- proof of work -----------------------------------------------------------


def test_pow_reproduces_the_reference_cycles() -> None:
    params = pow_mod.ChallengeParams.from_payload(FIXTURE_PARAMS)

    solutions = pow_mod.solve_challenges(FIXTURE_CHALLENGES, params)

    assert [solution["edgeIndices"] for solution in solutions] == FIXTURE_EDGES
    assert [solution["challenge"] for solution in solutions] == FIXTURE_CHALLENGES


def test_graph_is_deterministic_for_one_seed() -> None:
    params = pow_mod.ChallengeParams.from_payload(FIXTURE_PARAMS)

    first = pow_mod.build_graph(params)
    second = pow_mod.build_graph(params)

    assert first.offsets == second.offsets
    assert first.neighbors == second.neighbors
    assert first.edge_indices == second.edge_indices


def test_absurd_graph_sizes_are_refused() -> None:
    with pytest.raises(SensorError):
        pow_mod.ChallengeParams.from_payload({**FIXTURE_PARAMS, "NODES_BITS": 60})
    with pytest.raises(SensorError):
        pow_mod.ChallengeParams.from_payload({**FIXTURE_PARAMS, "CYCLE_LENGTH": 8, "EDGES_BITS": 6})
    with pytest.raises(SensorError):
        pow_mod.ChallengeParams.from_payload({"NODES_BITS": 14, "EDGES_BITS": 16, "CYCLE_LENGTH": 8})


def test_a_non_hex_challenge_is_refused() -> None:
    params = pow_mod.ChallengeParams.from_payload(FIXTURE_PARAMS)
    with pytest.raises(SensorError):
        pow_mod.compute_start_index("not-hex", params.nodes)
    with pytest.raises(SensorError):
        pow_mod.solve_challenges([], params)


# --- fingerprint -------------------------------------------------------------


async def test_the_fingerprint_header_is_deterministic_and_complete() -> None:
    builder = BotDeflectorBuilder()

    first = await builder.build(make_ctx())
    second = await builder.build(make_ctx())

    assert first["headers"][HEADER_FINGERPRINT] == second["headers"][HEADER_FINGERPRINT]
    blob = json.loads(first["headers"][HEADER_FINGERPRINT])
    assert blob["userAgent"] == UA
    assert blob["language"] == "en-US"
    assert blob["platform"] == "Win32"
    assert blob["screen"]["width"] == 1920
    assert isinstance(blob["timezoneOffset"], int)


async def test_a_client_capture_wins_over_profile_defaults() -> None:
    provided = {"userAgent": "Mozilla/5.0 custom", "screen": {"width": 1280, "height": 720}, "timezone": "Europe/Berlin"}

    plan = await BotDeflectorBuilder().build(make_ctx(fingerprint=provided))

    blob = json.loads(plan["headers"][HEADER_FINGERPRINT])
    assert blob["userAgent"] == "Mozilla/5.0 custom"
    assert blob["screen"] == {"width": 1280, "height": 720}
    assert blob["timezone"] == "Europe/Berlin"


async def test_no_user_agent_is_a_sensor_error() -> None:
    with pytest.raises(SensorError):
        await BotDeflectorBuilder().build(
            BuildContext(
                engine=AntibotEngine.BOTDEFLECTOR,
                site_url="https://botdeflector.eu/",
                fingerprint=None,
                behavior=None,
            )
        )


def test_builder_constants_point_at_the_two_deployments() -> None:
    assert DEFAULT_DOMAIN == "https://botdeflector.eu"
    assert QUEUE_IT_HOST == "https://ipv4.challenge.queue-it.net"
