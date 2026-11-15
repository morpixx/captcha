"""Lane routing contract tests.

The lane is a property of the type (domain/catalog.py); pick_lane is the only
place an internal preferred_mode may move it. A wrong row is not a 500: the
gateway sells the type, a worker with no matching solver burns the envelope to
ERR_UNSOLVABLE and refunds the client. These tests pin the routing rule plus
the two catalog transport flags the rule reads.
"""

from __future__ import annotations

import uuid

import pytest

from captcha.domain.catalog import (
    CATALOG,
    LANES,
    browser_capable,
    lane_for,
    requires_browser,
    types_by_lane,
)
from captcha.domain.enums import CaptchaType, SolveMode
from captcha.domain.lanes import pick_lane
from captcha.domain.task import Task

#: The 62 vendor types are the program; hopscotch is the custom extra.
VENDOR_TYPES = frozenset(t for t in CaptchaType if t is not CaptchaType.HOPSCOTCH)

#: Types whose answer is only minted inside their own widget.
BROWSER_ONLY = {
    CaptchaType.PROSOPO,
    CaptchaType.CAPTCHAFOX,
    CaptchaType.YANDEX,
    CaptchaType.TENCENT,
    CaptchaType.YIDUN,
    CaptchaType.ATBCAPTCHA,
}

#: HTTP-lane types that cannot finish without a live browser worker.
ESCALATION_TYPES = {
    CaptchaType.TURNSTILE,
    CaptchaType.TURNSTILE_INVISIBLE,
    CaptchaType.CF_CHALLENGE,
}


def _task(ct: CaptchaType, mode: SolveMode = SolveMode.AUTO) -> Task:
    return Task(id=uuid.uuid4(), type=ct, payload={}, preferred_mode=mode)


def test_catalog_has_63_rows() -> None:
    assert len(list(CaptchaType)) == 63
    assert set(CATALOG) == set(CaptchaType)
    assert len(VENDOR_TYPES) == 62


@pytest.mark.parametrize("ct", list(CaptchaType))
def test_default_lane_is_the_catalog_row(ct: CaptchaType) -> None:
    assert pick_lane(_task(ct)) == CATALOG[ct].lane
    assert pick_lane(_task(ct)) in LANES


@pytest.mark.parametrize("ct", list(CaptchaType))
def test_browser_hint_only_moves_browser_capable_types(ct: CaptchaType) -> None:
    lane = pick_lane(_task(ct, SolveMode.BROWSER))
    expected = "browser" if browser_capable(ct) else CATALOG[ct].lane
    assert lane == expected


@pytest.mark.parametrize("ct", list(CaptchaType))
def test_http_hint_probes_browser_types_over_http(ct: CaptchaType) -> None:
    expected = "http" if CATALOG[ct].lane == "browser" else CATALOG[ct].lane
    assert pick_lane(_task(ct, SolveMode.HTTP)) == expected


def test_browser_hint_cannot_strand_a_type_without_a_browser_solver() -> None:
    stranded = [ct.value for ct in CaptchaType if not browser_capable(ct) and pick_lane(_task(ct, SolveMode.BROWSER)) == "browser"]
    assert stranded == []


def test_browser_lane_types_are_browser_capable() -> None:
    for ct in types_by_lane("browser"):
        assert browser_capable(ct), ct.value


def test_escalation_types_stay_http_but_require_a_browser_worker() -> None:
    assert {ct for ct in CaptchaType if requires_browser(ct)} == ESCALATION_TYPES
    for ct in ESCALATION_TYPES:
        assert CATALOG[ct].lane == "http", ct.value
        assert browser_capable(ct), ct.value


def test_browser_only_vendors_live_in_the_browser_lane() -> None:
    assert types_by_lane("browser") >= BROWSER_ONLY
    for ct in BROWSER_ONLY:
        assert CATALOG[ct].lane == "browser", ct.value
        assert CATALOG[ct].browser is True


def test_hcaptcha_family_keeps_its_http_probe() -> None:
    for ct in (CaptchaType.HCAPTCHA, CaptchaType.HCAPTCHA_ENTERPRISE):
        assert CATALOG[ct].lane == "browser"
        assert pick_lane(_task(ct, SolveMode.HTTP)) == "http"
        assert pick_lane(_task(ct, SolveMode.BROWSER)) == "browser"


def test_funcaptcha_family_left_the_cv_lane() -> None:
    for ct in (CaptchaType.FUNCAPTCHA, CaptchaType.FUNCAPTCHA_ENTERPRISE):
        assert CATALOG[ct].lane == "http", ct.value
        assert browser_capable(ct), ct.value


def test_interactive_cv_types_with_a_cv_answer_step_moved_to_http() -> None:
    for ct in (CaptchaType.HUNT, CaptchaType.ALIBABA):
        assert CATALOG[ct].lane == "http", ct.value
        assert pick_lane(_task(ct, SolveMode.HTTP)) == "http"


def test_hopscotch_is_the_custom_cv_type() -> None:
    assert CATALOG[CaptchaType.HOPSCOTCH].lane == "cv"
    assert CATALOG[CaptchaType.HOPSCOTCH].browser is False
    assert CaptchaType.HOPSCOTCH not in VENDOR_TYPES


def test_every_lane_has_at_least_one_type() -> None:
    for lane in LANES:
        assert types_by_lane(lane), lane


def test_lane_counts_are_deliberate() -> None:
    counts = {lane: len(types_by_lane(lane)) for lane in LANES}
    assert counts == {"http": 25, "cv": 28, "protocol": 2, "browser": 8}
    assert sum(counts.values()) == 63


def test_lane_for_unknown_type_falls_back_to_http() -> None:
    class _FakeType:
        value = "__not_a_real_type__"

    assert lane_for(_FakeType()) == "http"  # type: ignore[arg-type]
    assert browser_capable(_FakeType()) is False  # type: ignore[arg-type]
    assert requires_browser(_FakeType()) is False  # type: ignore[arg-type]
