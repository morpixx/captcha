"""Contract tests for the shared prompt-conditioned grid head.

No torch, no PIL, no network: this file only exercises grid.py plus the static
SolverInfo claims of the five enrolled grid solvers.
"""

from __future__ import annotations

import pytest
from _support import make_meta, make_spec

from captcha.adapters.weights.meta import ModelMeta
from captcha.domain import CaptchaType
from captcha.domain.errors import ConfigError, ModelInferenceError, UnsolvableError
from captcha.solvers import SolverInfo
from captcha.solvers.classification.awswaf import AWSWAFSolver
from captcha.solvers.classification.base_grid import GridClassifier
from captcha.solvers.classification.funcaptcha_grid import FunCaptchaGridSolver
from captcha.solvers.classification.grid import (
    HEADS,
    SHAPE_2X3,
    SHAPE_3X3,
    SHAPE_4X4,
    coerce_threshold,
    head_contract,
    resolve_grid,
)
from captcha.solvers.classification.hcaptcha import HCaptchaSolver
from captcha.solvers.classification.head_meta import head_spec_from_meta
from captcha.solvers.classification.prompt import normalize_text, resolve_label
from captcha.solvers.classification.recaptcha_grid import RecaptchaGridSolver
from captcha.solvers.classification.shein import SheinSolver

GRID_SOLVERS = (HCaptchaSolver, RecaptchaGridSolver, FunCaptchaGridSolver, AWSWAFSolver, SheinSolver)


def _meta(**overrides: object) -> ModelMeta:
    values: dict[str, object] = {
        "name": "hcaptcha-v1",
        "version": "v1",
        "framework": "safetensors",
        "s3_key": "hcaptcha-v1/v1/weights.safetensors",
        "sha256": "0" * 64,
    }
    values.update(overrides)
    return ModelMeta(**values)  # type: ignore[arg-type]


# ---- head catalog ----------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "shapes"),
    [
        ("hcaptcha-v1", ("3x3",)),
        ("recaptcha-v1", ("3x3", "4x4")),
        ("funcap-v1", ("2x3",)),
        ("awswaf-v1", ("3x3",)),
        ("shein-v1", ("3x3",)),
    ],
)
def test_head_catalog_shapes(head: str, shapes: tuple[str, ...]) -> None:
    contract = HEADS[head]
    assert tuple(str(shape) for shape in contract.shapes) == shapes
    assert contract.default_shape in contract.shapes


def test_recaptcha_accepts_both_grids_hcaptcha_only_3x3() -> None:
    recaptcha = head_contract("recaptcha-v1")
    assert recaptcha.resolve(4, 4) == SHAPE_4X4
    assert recaptcha.resolve("3", "3") == SHAPE_3X3

    with pytest.raises(UnsolvableError, match="does not serve"):
        head_contract("hcaptcha-v1").resolve(4, 4)
    with pytest.raises(UnsolvableError, match="not supported"):
        head_contract("hcaptcha-v1").resolve(5, 5)
    with pytest.raises(ConfigError, match="unknown classification head"):
        head_contract("nope-v1")


# ---- SolverInfo claims -----------------------------------------------------


@pytest.mark.parametrize("solver", GRID_SOLVERS)
def test_grid_solvers_never_claim_hcaptcha_token_types(solver: type[GridClassifier]) -> None:
    assert CaptchaType.HCAPTCHA not in solver.info.types
    assert CaptchaType.HCAPTCHA_ENTERPRISE not in solver.info.types


@pytest.mark.parametrize("solver", (HCaptchaSolver, RecaptchaGridSolver, FunCaptchaGridSolver))
def test_pure_grid_solvers_claim_only_classification_grid(solver: type[GridClassifier]) -> None:
    assert solver.info.types == (CaptchaType.CLASSIFICATION_GRID,)
    assert solver.head.name in HEADS


def test_vendor_grid_solvers_keep_their_own_type() -> None:
    # These two are the only enrolled solvers for their types; keeping the claim
    # preserves 62/62 coverage until a token/coordinate wrapper exists.
    assert AWSWAFSolver.info.types == (CaptchaType.AWSWAF,)
    assert SheinSolver.info.types == (CaptchaType.SHEIN,)


def test_grid_solver_rejects_hcaptcha_claim_at_class_creation() -> None:
    with pytest.raises(TypeError, match="cannot claim token type"):

        class BadGrid(GridClassifier):
            info = SolverInfo(name="bad-grid", types=(CaptchaType.HCAPTCHA,))
            head = HEADS["hcaptcha-v1"]


def test_grid_solver_requires_head_contract() -> None:
    with pytest.raises(TypeError, match="must set head"):

        class NoHead(GridClassifier):
            info = SolverInfo(name="no-head", types=(CaptchaType.CLASSIFICATION_GRID,))


# ---- prompt matching -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BUS", "bus"),
        ("  a bus  ", "bus"),
        ("Select all images with a traffic light", "traffic light"),
        ("pick all squares with crosswalks", "crosswalks"),
        ("The  Fire-Hydrant", "fire hydrant"),
        ("click on the bicycle", "bicycle"),
    ],
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("select all images with a traffic light", "traffic light"),
        ("traffic lights", "traffic light"),
        ("TRAFFIC_LIGHT", "traffic light"),
        ("a red bus", "bus"),
        ("buses", "bus"),
    ],
)
def test_resolve_label_shapes(prompt: str, expected: str) -> None:
    labels = ("bus", "traffic light", "crosswalk")
    assert resolve_label(prompt, labels, head="hcaptcha-v1") == labels.index(expected)


def test_resolve_label_alias_and_plural() -> None:
    labels = ("bus", "traffic light")
    aliases = {"trafficlight": "traffic light", "coach": "bus"}
    assert resolve_label("traffic light", labels, aliases) == 1
    assert resolve_label("trafficlight", labels, aliases) == 1
    assert resolve_label("coaches", labels, aliases) == 0


def test_resolve_label_unknown_and_ambiguous() -> None:
    with pytest.raises(ModelInferenceError, match="no label matching"):
        resolve_label("motorcycle", ("bus", "car"))
    with pytest.raises(ModelInferenceError, match="more than one"):
        resolve_label("big red car blue car", ("red car", "blue car"))


# ---- threshold / selection -------------------------------------------------


def test_select_uses_threshold_and_keeps_empty_answer() -> None:
    spec = make_spec(HEADS["hcaptcha-v1"], ["bus"])
    probs = [0.9, 0.1, 0.5, 0.49, 0.0, 0.0, 0.0, 0.0, 0.0]

    assert spec.select(probs) == [0, 2]
    assert spec.select(probs, threshold=0.95) == []
    assert spec.select(probs, threshold=0.0) == list(range(9))


def test_select_rejects_wrong_tile_count() -> None:
    spec = make_spec(HEADS["hcaptcha-v1"], ["bus"])
    with pytest.raises(ModelInferenceError, match="tile count"):
        spec.select([0.9, 0.1])


@pytest.mark.parametrize("bad", [-0.1, 1.1, "wide", float("nan")])
def test_payload_threshold_rejects_out_of_range(bad: object) -> None:
    with pytest.raises(UnsolvableError, match="threshold"):
        coerce_threshold(bad, source="payload")
    with pytest.raises(ConfigError, match="threshold"):
        coerce_threshold(bad, source="meta")


# ---- meta / bundle parsing -------------------------------------------------


def test_meta_schema_labels_threshold_grid() -> None:
    contract = HEADS["recaptcha-v1"]
    spec = head_spec_from_meta(
        contract,
        make_meta(contract, ["Bus", "Crosswalk"], threshold=0.42, shape=SHAPE_4X4),
    )
    assert spec.labels == ("bus", "crosswalk")
    assert spec.threshold == pytest.approx(0.42)
    assert spec.shape == SHAPE_4X4
    assert spec.resolve("crosswalks") == 1


def test_meta_labels_map_is_sorted_by_position() -> None:
    meta = _meta(labels={"Bus": "1", "Car": "0"}, input_schema={"threshold": 0.3})
    spec = head_spec_from_meta(HEADS["hcaptcha-v1"], meta)
    assert spec.labels == ("car", "bus")
    assert spec.threshold == pytest.approx(0.3)
    assert spec.shape == SHAPE_3X3


def test_bundle_config_supplies_schema() -> None:
    meta = _meta(labels={}, input_schema={})
    spec = head_spec_from_meta(
        HEADS["recaptcha-v1"],
        meta,
        config={"labels": ["bus", "car"], "threshold": 0.35, "grid": "4x4"},
    )
    assert spec.labels == ("bus", "car")
    assert spec.threshold == pytest.approx(0.35)
    assert spec.shape == SHAPE_4X4


def test_input_schema_wins_over_bundle_config() -> None:
    contract = HEADS["hcaptcha-v1"]
    meta = make_meta(contract, ["bus"], threshold=0.6)
    spec = head_spec_from_meta(contract, meta, config={"labels": ["car"], "threshold": 0.2})
    assert spec.labels == ("bus",)
    assert spec.threshold == pytest.approx(0.6)


@pytest.mark.parametrize(
    "meta",
    [
        _meta(labels={}, input_schema={}),
        _meta(labels={"Bus": "0", "BUS": "1"}),
        _meta(labels={"bus": "0", "car": "2"}),
        _meta(labels={"bus": "not-an-int"}),
        _meta(labels={"bus": "0"}, input_schema={"grid": {"rows": 2, "cols": 3}}),
        _meta(labels={"bus": "0"}, input_schema={"prompt_aliases": {"car": "car"}}),
        _meta(labels={"bus": "0"}, input_schema={"threshold": 1.5}),
    ],
)
def test_broken_head_meta_is_config_error(meta: ModelMeta) -> None:
    with pytest.raises(ConfigError):
        head_spec_from_meta(HEADS["hcaptcha-v1"], meta)


# ---- shape resolution ------------------------------------------------------


def test_resolve_grid_prefers_payload_and_validates() -> None:
    recaptcha = HEADS["recaptcha-v1"]

    assert resolve_grid({}, recaptcha, fallback=recaptcha.default_shape) == SHAPE_3X3
    assert resolve_grid({"rows": 4, "cols": 4}, recaptcha, fallback=SHAPE_3X3) == SHAPE_4X4
    with pytest.raises(UnsolvableError, match="together"):
        resolve_grid({"rows": 3}, recaptcha, fallback=SHAPE_3X3)
    with pytest.raises(UnsolvableError, match="does not serve"):
        resolve_grid({"rows": 2, "cols": 3}, recaptcha, fallback=SHAPE_3X3)
    assert resolve_grid({}, HEADS["funcap-v1"], fallback=SHAPE_2X3) == SHAPE_2X3
