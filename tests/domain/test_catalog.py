"""Contract tests for the single per-type metadata table.

The catalog is the single source for (lane, price, solution_kind). If a type
disappears from any of these projections, the gateway miscounts billing, the
queue routes to a dead lane, or the client gets the wrong solution shape.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from captcha.domain.catalog import (
    CATALOG,
    DEFAULT_PRICE,
    LANES,
    SolutionKind,
    all_specs,
    lane_for,
    price,
    solution_kind,
    spec,
    types_by_lane,
    types_by_solution_kind,
)
from captcha.domain.enums import CaptchaType
from captcha.domain.lanes import pick_lane
from captcha.domain.pricing import estimate_cost
from captcha.domain.task import Task


def _task(ct: CaptchaType) -> Task:
    return Task(id=__import__("uuid").uuid4(), type=ct, payload={})


def test_every_type_has_a_row() -> None:
    """A new CaptchaType without a catalog row would ship silently otherwise."""
    missing = set(CaptchaType) - set(CATALOG)
    assert not missing, f"types missing from catalog: {[m.value for m in missing]}"


def test_no_duplicate_rows_in_insertion_order() -> None:
    """all_specs() returns _ROWS; a duplicate would pass the dict check but inflate order."""
    seen = set()
    for row in all_specs():
        assert row.type not in seen, f"duplicate row for {row.type.value}"
        seen.add(row.type)


def test_all_rows_have_valid_lane() -> None:
    for row in all_specs():
        assert row.lane in LANES, f"{row.type.value} has invalid lane {row.lane!r}"


def test_all_rows_have_positive_price() -> None:
    for row in all_specs():
        assert row.price > Decimal("0"), f"{row.type.value} has non-positive price {row.price}"


def test_all_rows_have_valid_solution_kind() -> None:
    kinds = set(SolutionKind)
    for row in all_specs():
        assert row.solution_kind in kinds


@pytest.mark.parametrize("ct", list(CaptchaType))
def test_spec_matches_pricing_and_lanes(ct: CaptchaType) -> None:
    """`pricing.estimate_cost` and `lanes.pick_lane` must read what the catalog says."""
    row = spec(ct)
    assert estimate_cost(ct) == row.price, f"estimate_cost disagrees for {ct.value}"
    assert pick_lane(_task(ct)) == row.lane, f"pick_lane disagrees for {ct.value}"


def test_lane_for_falls_back_to_http() -> None:
    """A type absent from the catalog should still route somewhere valid."""

    class _FakeType:
        value = "__not_a_real_type__"

    assert lane_for(_FakeType()) == "http"  # type: ignore[arg-type]


def test_price_falls_back_to_default() -> None:
    class _FakeType:
        value = "__not_a_real_type__"

    assert price(_FakeType()) == DEFAULT_PRICE  # type: ignore[arg-type]


def test_solution_kind_is_none_for_unknown() -> None:
    class _FakeType:
        value = "__not_a_real_type__"

    assert solution_kind(_FakeType()) is None  # type: ignore[arg-type]


def test_types_by_lane_partitions_the_catalog() -> None:
    all_by_lane: set[CaptchaType] = set()
    for lane in LANES:
        all_by_lane |= types_by_lane(lane)
    assert all_by_lane == set(CATALOG), "types_by_lane missed something"


def test_types_by_solution_kind_partitions_the_catalog() -> None:
    all_by_kind: set[CaptchaType] = set()
    for kind in SolutionKind:
        all_by_kind |= types_by_solution_kind(kind)
    assert all_by_kind == set(CATALOG), "types_by_solution_kind missed something"


def test_protocol_lane_is_pow_only() -> None:
    assert types_by_lane("protocol") == {CaptchaType.FRIENDLY, CaptchaType.ALTCHA}


CV_PRIMITIVES = {
    CaptchaType.TEXT,
    CaptchaType.MATH_IMAGE,
    CaptchaType.IMAGE_TO_TEXT,
    CaptchaType.AUDIO,
    CaptchaType.ROTATE,
    CaptchaType.SLIDER,
    CaptchaType.CLICK,
    CaptchaType.COORDINATES,
    CaptchaType.BOUNDING_BOX,
    CaptchaType.CLASSIFICATION_GRID,
    CaptchaType.DRAW_AROUND,
    CaptchaType.DRAG_DROP,
}

#: Types that are pure protocol: no torch, no opencv, no model registry.
HTTP_ONLY = {
    CaptchaType.RECAPTCHA_V3,
    CaptchaType.RECAPTCHA_V3_ENTERPRISE,
    CaptchaType.FUNCAPTCHA_BDA,
    CaptchaType.TURNSTILE,
    CaptchaType.TURNSTILE_INVISIBLE,
    CaptchaType.CF_CHALLENGE,
    CaptchaType.AKAMAI_SENSOR,
    CaptchaType.AKAMAI_SBSD,
    CaptchaType.DATADOME_FULL,
    CaptchaType.DATADOME_SLIDER,
    CaptchaType.KASADA,
    CaptchaType.PERIMETERX,
    CaptchaType.IMPERVA,
    CaptchaType.TSPD,
    CaptchaType.TEMU,
    CaptchaType.CASTLE,
    CaptchaType.CYBERSIARA,
    CaptchaType.TWITCH_INTEGRITY,
    CaptchaType.BOTGUARD,
    CaptchaType.BOTDEFLECTOR,
}


def test_cv_lane_holds_the_ml_primitives() -> None:
    """Everything that needs torch/opencv/whisper must be reachable only from a
    worker built with --extra cv."""
    assert types_by_lane("cv") >= CV_PRIMITIVES


def test_http_lane_holds_the_protocol_only_types() -> None:
    """A protocol solver on the cv lane would pin an I/O worker to a GPU node."""
    assert types_by_lane("http") >= HTTP_ONLY


def test_cv_and_http_lanes_do_not_overlap() -> None:
    assert not (types_by_lane("cv") & types_by_lane("http"))


def test_recaptcha_family_shares_solution_kind() -> None:
    assert types_by_solution_kind(SolutionKind.RECAPTCHA) == {
        CaptchaType.RECAPTCHA_V2,
        CaptchaType.RECAPTCHA_V2_ENTERPRISE,
        CaptchaType.RECAPTCHA_V2_INVISIBLE,
        CaptchaType.RECAPTCHA_V2_CALLBACK,
        CaptchaType.RECAPTCHA_V3,
        CaptchaType.RECAPTCHA_V3_ENTERPRISE,
    }


def test_cookie_engines_group_together() -> None:
    assert types_by_solution_kind(SolutionKind.COOKIE_ENGINE) == {
        CaptchaType.DATADOME_FULL,
        CaptchaType.DATADOME_SLIDER,
        CaptchaType.AKAMAI_SENSOR,
        CaptchaType.AKAMAI_SBSD,
        CaptchaType.KASADA,
        CaptchaType.PERIMETERX,
        CaptchaType.IMPERVA,
        CaptchaType.TSPD,
        CaptchaType.TEMU,
    }


def test_data_nest_family() -> None:
    assert types_by_solution_kind(SolutionKind.DATA_NEST) == {
        CaptchaType.HUNT,
        CaptchaType.CASTLE,
        CaptchaType.BASILISK,
        CaptchaType.ALIBABA,
        CaptchaType.TENCENT,
    }
