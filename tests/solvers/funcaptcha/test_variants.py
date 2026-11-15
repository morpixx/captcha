from __future__ import annotations

import pytest
from helpers import StubRegistry, fixture, make_task, stub_class

from captcha.domain import CaptchaType, SolverResult
from captcha.domain.errors import ConfigError, UnsolvableError
from captcha.solvers import SolverContext
from captcha.solvers.funcaptcha import variants


def grid_result() -> SolverResult:
    return SolverResult(indices=[0, 2], extra={"head": "funcap-v1"})


def rotate_result() -> SolverResult:
    return SolverResult(angle=362.7)


# --- classification ----------------------------------------------------------


@pytest.mark.parametrize("game", ["rotate", "3d_rollball_animals", "rollball"])
def test_rotate_games_map_to_the_geometry_variant(game: str):
    assert variants.classify({"game_type": game}, {}) == "rotate"


@pytest.mark.parametrize("game", ["image_label_binary", "tiles", "rockstack"])
def test_grid_games_map_to_the_grid_variant(game: str):
    assert variants.classify({"game_type": game}, {}) == "grid"


@pytest.mark.parametrize("game", ["match_key", "hand_number", "image_matching"])
def test_matching_games_map_to_the_match_variant(game: str):
    assert variants.classify({"game_type": game}, {}) == "match"


def test_unknown_game_with_images_and_instruction_falls_back_to_grid():
    challenge = fixture("gfct_grid.json")
    challenge.pop("game_type")

    assert variants.classify(challenge, {}) == "grid"


def test_unknown_game_without_a_question_is_unsupported():
    challenge = {"challengeID": "x", "_challenge_imgs": ["a"]}

    assert variants.classify(challenge, {}) == "unsupported"


def test_payload_can_override_the_variant():
    assert variants.classify({"game_type": "mystery"}, {"variant": "rotate"}) == "rotate"


def test_game_type_comes_from_the_challenge_first():
    assert variants.game_type_of({"gameType": "tiles"}, {"game_type": "rotate"}) == "tiles"


# --- dispatch ----------------------------------------------------------------


async def test_grid_dispatch_builds_the_classifier_payload():
    stub = stub_class("funcaptcha_grid", grid_result())
    registry = StubRegistry([stub])
    task = make_task()

    result = await variants.dispatch(
        registry,
        task,
        SolverContext(),
        variant="grid",
        challenge=fixture("gfct_grid.json"),
        payload={"rows": 2, "cols": 3},
    )

    assert result.indices == [0, 2]
    assert result.extra["head"] == "funcap-v1"


async def test_dispatch_passes_image_prompt_and_grid_shape():
    stub = stub_class("funcaptcha_grid", grid_result())
    instance = stub()
    registry = StubRegistry([stub])
    task = make_task()

    await variants.dispatch(
        registry,
        task,
        SolverContext(),
        variant="grid",
        challenge=fixture("gfct_grid.json"),
        payload={"rows": 2, "cols": 3},
    )

    sub_task = instance.tasks[0]
    assert sub_task.type is CaptchaType.CLASSIFICATION_GRID
    assert sub_task.payload["prompt"] == "Pick the bus"
    assert sub_task.payload["rows"] == 2
    assert sub_task.payload["cols"] == 3
    assert sub_task.payload["head"] == "funcap-v1"
    assert sub_task.payload["challenge_id"] == "713184db7d62a8982.4535671902"
    assert sub_task.payload["image_b64"].startswith("iVBORw0KGgo")
    assert instance.prepared is True
    assert instance.shut_down is True


async def test_match_dispatch_uses_the_head_override():
    stub = stub_class("funcaptcha_grid", grid_result())
    instance = stub()
    registry = StubRegistry([stub])

    await variants.dispatch(
        registry,
        make_task(),
        SolverContext(),
        variant="match",
        challenge={**fixture("gfct_grid.json"), "game_type": "match_key"},
        payload={"match_head": "funcap-match-v1"},
    )

    assert instance.tasks[0].payload["head"] == "funcap-match-v1"


async def test_rotate_dispatch_builds_the_geometry_payload():
    stub = stub_class("rotate", rotate_result())
    instance = stub()
    registry = StubRegistry([stub])

    result = await variants.dispatch(
        registry,
        make_task(),
        SolverContext(),
        variant="rotate",
        challenge=fixture("gfct_rotate.json"),
        payload={"target_angle": 90},
    )

    assert result.angle == 362.7
    sub_task = instance.tasks[0]
    assert sub_task.type is CaptchaType.ROTATE
    assert sub_task.payload["image"].startswith("iVBORw0KGgo")
    assert sub_task.payload["target_angle"] == 90


async def test_pick_is_the_fallback_when_no_named_solver_exists():
    stub = stub_class("some_grid", grid_result(), types=(CaptchaType.CLASSIFICATION_GRID,))
    registry = StubRegistry([stub])

    result = await variants.dispatch(
        registry,
        make_task(),
        SolverContext(),
        variant="grid",
        challenge=fixture("gfct_grid.json"),
        payload={},
    )

    assert result.indices == [0, 2]


async def test_missing_registry_is_config_error():
    with pytest.raises(ConfigError):
        await variants.dispatch(
            None,
            make_task(),
            SolverContext(),
            variant="grid",
            challenge=fixture("gfct_grid.json"),
            payload={},
        )


async def test_missing_solver_is_unsolvable():
    with pytest.raises(UnsolvableError):
        await variants.dispatch(
            StubRegistry([]),
            make_task(),
            SolverContext(),
            variant="rotate",
            challenge=fixture("gfct_rotate.json"),
            payload={},
        )


async def test_unknown_variant_is_unsolvable():
    with pytest.raises(UnsolvableError):
        await variants.dispatch(
            StubRegistry([]), make_task(), SolverContext(), variant="mystery", challenge={}, payload={}
        )


# --- answers -----------------------------------------------------------------


def test_grid_answer_is_the_tile_list():
    assert variants.answer_for(SolverResult(indices=[0, 2]), "grid") == [0, 2]


def test_match_answer_is_the_tile_list():
    assert variants.answer_for(SolverResult(indices=[4]), "match") == [4]


def test_rotate_answer_wraps_to_360():
    assert variants.answer_for(SolverResult(angle=362.7), "rotate") == [3]
    assert variants.answer_for(SolverResult(angle=-10.0), "rotate") == [350]


def test_missing_answer_is_unsolvable():
    with pytest.raises(UnsolvableError):
        variants.answer_for(SolverResult(), "grid")
    with pytest.raises(UnsolvableError):
        variants.answer_for(SolverResult(), "rotate")


def test_grid_round_without_images_is_unsolvable():
    with pytest.raises(UnsolvableError):
        variants._grid_payload({"instruction": "x"}, {})
