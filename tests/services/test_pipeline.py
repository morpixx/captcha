"""Pipeline hooks and the golden harness.

Sampler: hooks must not change sampling for types without one, a broken hook must
not lose the sample, and the report must say which types are visual.
Golden: accuracy is measured over answered cases, coverage over all of them, and
a predictor that blows up on one case must not abort the other 399.

Everything runs against a fake S3 writer and a fake DB session. No network, no
MinIO, no Postgres.
"""

from __future__ import annotations

import base64
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from captcha.adapters.storage import Task
from captcha.domain.errors import ConfigError
from captcha.services.pipeline import sampler as sampler_mod
from captcha.services.pipeline.golden import (
    GoldenManifest,
    GoldenReport,
    run_golden,
)
from captcha.services.pipeline.golden_match import mismatched_fields
from captcha.services.pipeline.sampler import (
    VISUAL_TYPES,
    Sampler,
    compact_image_hook,
)

# ---- fakes -----------------------------------------------------------------


class FakeS3Writer:
    def __init__(self) -> None:
        self.objects: dict[str, Any] = {}

    async def put_json(self, key: str, obj: Any, *, content_type: str = "application/json") -> bool:
        self.objects[key] = obj
        return True


class _Rows:
    def __init__(self, rows: list[Task]) -> None:
        self._rows = rows

    def scalars(self) -> _Rows:
        return self

    def all(self) -> list[Task]:
        return list(self._rows)


class FakeSession:
    def __init__(self, rows: list[Task]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _Rows:
        self.statements.append(stmt)
        return _Rows(self.rows)


@asynccontextmanager
async def _cm(session: FakeSession):
    yield session


def _session_factory(rows: list[Task]):
    def factory() -> Any:
        return _cm(FakeSession(rows))

    return factory


def _task(type_: str, *, payload: dict[str, Any] | None = None, result: dict[str, Any] | None = None) -> Task:
    return Task(
        id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        type=type_,
        status="ready",
        payload=payload or {},
        result=result,
        retries=0,
        created_at=datetime.now(UTC),
    )


def _keys_with(s3: FakeS3Writer, needle: str) -> list[str]:
    return [key for key in s3.objects if needle in key]


# ---- sampler: hooks --------------------------------------------------------


async def test_sampler_applies_hook_and_reports_visual_types(monkeypatch: pytest.MonkeyPatch) -> None:
    image = "A" * 5000
    task = _task("hopscotch", payload={"image_b64": image, "hint": "5 targets"})
    s3 = FakeS3Writer()
    sampler = Sampler(
        s3=s3,
        types=["hopscotch", "rotate"],
        hooks={"hopscotch": compact_image_hook(max_bytes=1024)},
        report_hooks=[_note_hook],
    )
    monkeypatch.setattr(sampler_mod, "async_session", _session_factory([task]))

    report = await sampler.run_once()

    assert report.per_type == {"hopscotch": 1, "rotate": 1}
    assert report.total_sampled == 2
    assert report.failures == {}

    hop = s3.objects[_keys_with(s3, "/hopscotch/")[0]]
    assert hop["payload"]["image_b64"] is None
    assert hop["payload"]["image_b64_omitted"]["chars"] == len(image)
    assert len(hop["payload"]["image_b64_omitted"]["sha256"]) == 64
    assert hop["payload"]["hint"] == "5 targets"

    rotate = s3.objects[_keys_with(s3, "/rotate/")[0]]
    assert rotate["payload"]["image_b64"] == image, "a type without a hook keeps its payload"

    assert task.payload["image_b64"] == image, "the ORM row must not be rewritten by a hook"

    data = report.to_dict()
    assert data["visual"] == {"types": ["hopscotch", "rotate"], "sampled": 2}
    assert data["extra"] == {"note": "visual sample"}
    assert data["hook_failures"] == {}
    assert data["total_sampled"] == 2
    assert data["per_type"] == {"hopscotch": 1, "rotate": 1}


async def test_sampler_hook_failure_keeps_the_raw_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task("hopscotch", payload={"image_b64": "A" * 4096})
    s3 = FakeS3Writer()

    def boom(_task: Task, _blob: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("hook exploded")

    sampler = Sampler(s3=s3, types=["hopscotch"], hooks={"hopscotch": boom})
    monkeypatch.setattr(sampler_mod, "async_session", _session_factory([task]))

    report = await sampler.run_once()

    blob = s3.objects[_keys_with(s3, "/hopscotch/")[0]]
    assert blob["payload"]["image_b64"] == "A" * 4096
    assert report.per_type == {"hopscotch": 1}
    assert str(task.id) in report.hook_failures
    assert "hook exploded" in report.hook_failures[str(task.id)]


async def test_sampler_report_hook_failure_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task("hcaptcha", payload={"image_b64": "A"})
    sampler = Sampler(
        s3=FakeS3Writer(),
        types=["hcaptcha"],
        report_hooks=[_boom_hook, _note_hook],
    )
    monkeypatch.setattr(sampler_mod, "async_session", _session_factory([task]))

    data = (await sampler.run_once()).to_dict()

    assert data["extra"]["note"] == "visual sample", "a later hook still runs"
    assert "_boom_hook" in data["extra"]["report_hook_errors"]


async def test_sampler_records_per_type_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sampler = Sampler(s3=FakeS3Writer(), types=["hopscotch"])
    monkeypatch.setattr(sampler_mod, "async_session", _session_factory([]))

    async def broken(_type: str, _n: int, *, since: Any = None) -> int:
        raise RuntimeError("db gone")

    monkeypatch.setattr(sampler, "sample_live_traffic", broken)

    report = await sampler.run_once()

    assert report.per_type == {}
    assert report.failures == {"hopscotch": "db gone"}


def test_visual_types_cover_wire_names() -> None:
    assert {"hopscotch", "hcaptcha", "classification_grid", "rotate", "image_to_text"} <= VISUAL_TYPES
    assert "text_captcha" not in VISUAL_TYPES
    assert "friendly_captcha" not in VISUAL_TYPES


def test_sampler_visual_types_filters_configured_list() -> None:
    sampler = Sampler(s3=FakeS3Writer(), types=["hopscotch", "rotate", "friendly_captcha"])
    assert sampler.visual_types() == ["hopscotch", "rotate"]


def test_sampler_from_env_installs_compaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPELINE_SAMPLE_COMPACT_IMAGES", "1")
    hooks_on = Sampler.from_env(FakeS3Writer()).hooks
    assert "hopscotch" in hooks_on
    assert "hcaptcha" in hooks_on
    assert "friendly_captcha" not in hooks_on

    monkeypatch.setenv("PIPELINE_SAMPLE_COMPACT_IMAGES", "0")
    assert Sampler.from_env(FakeS3Writer()).hooks == {}


def test_compact_image_hook_leaves_small_and_odd_payloads_alone() -> None:
    hook = compact_image_hook(max_bytes=1024)
    task = _task("hopscotch")

    small = {"payload": {"image_b64": "x" * 10}}
    assert hook(task, small) is small

    odd = {"payload": "not a mapping"}
    assert hook(task, odd) is odd

    numeric = {"payload": {"image_b64": 12345}}
    assert hook(task, numeric) is numeric

    custom = compact_image_hook(max_bytes=1024, field_name="piece")
    blob = {"payload": {"piece": "x" * 4096}}
    assert custom(task, blob)["payload"]["piece"] is None
    assert custom(task, blob)["payload"]["piece_omitted"]["chars"] == 4096


def _note_hook(report: Any) -> dict[str, Any]:
    assert report.total_sampled >= 0
    return {"note": "visual sample"}


def _boom_hook(_report: Any) -> dict[str, Any]:
    raise RuntimeError("report hook broke")


# ---- golden: manifest ------------------------------------------------------


MANIFEST: dict[str, Any] = {
    "artifact": "hopscotch-v1",
    "version": "v1",
    "tolerance_px": 8,
    "cases": [
        {"id": "0001", "type": "hopscotch", "payload": {"case": "a"}, "expected": {"index": 1, "coordinates": [100, 100]}},
        {
            "id": "0002",
            "type": "hopscotch",
            "payload": {"case": "b"},
            "expected": {"index": 1, "coordinates": [100, 100]},
            "tolerance_px": 2,
        },
        {"id": "0003", "type": "hopscotch", "payload": {"case": "c"}, "expected": {"index": 0}},
    ],
}


def test_manifest_parses_cases_and_defaults() -> None:
    manifest = GoldenManifest.from_mapping(MANIFEST)

    assert manifest.artifact == "hopscotch-v1"
    assert manifest.total == 3
    assert manifest.tolerance_px == 8
    assert manifest.min_accuracy == 0.95
    assert manifest.min_coverage == 0.80
    assert manifest.cases[0].tolerance_px == 8
    assert manifest.cases[1].tolerance_px == 2, "per-case tolerance wins"
    assert manifest.cases[2].type_ == "hopscotch"


@pytest.mark.parametrize(
    ("doc", "match"),
    [
        ({"cases": []}, "non-empty"),
        ({"cases": [{"id": "x", "expected": [1, 2]}]}, "expected"),
        ({"cases": [{"id": "x", "expected": {}}]}, "expected"),
        ({"cases": [{"id": "x", "expected": {"index": 0}}, {"id": "x", "expected": {"index": 1}}]}, "duplicate"),
        ({"cases": [{"id": "x", "expected": {"index": 0}, "payload": 3}]}, "payload"),
        ({"cases": [{"id": "x", "expected": {"index": 0}, "image": 7}]}, "image"),
        ({"cases": [{"id": "x", "expected": {"index": 0}, "tags": "gold"}]}, "tags"),
        ({"cases": [{"id": "x", "expected": {"index": 0}, "tolerance_px": "wide"}]}, "tolerance_px"),
    ],
)
def test_manifest_rejects_broken_documents(doc: dict[str, Any], match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        GoldenManifest.from_mapping(doc)


def test_manifest_derives_ids_when_absent() -> None:
    manifest = GoldenManifest.from_mapping({"cases": [{"expected": {"index": 0}}]})
    assert manifest.cases[0].case_id == "case-0000"


def test_manifest_rejects_invalid_json_bytes() -> None:
    with pytest.raises(ConfigError, match="not valid JSON"):
        GoldenManifest.from_bytes(b"{nope")


def test_manifest_loads_from_file(tmp_path: Any) -> None:
    import json

    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")

    manifest = GoldenManifest.from_file(path)

    assert manifest.total == 3
    assert manifest.source == str(path)
    with pytest.raises(ConfigError, match="cannot read"):
        GoldenManifest.from_file(tmp_path / "absent.json")


# ---- golden: run -----------------------------------------------------------


async def test_run_golden_reports_accuracy_and_coverage() -> None:
    answers = {
        "a": {"index": 1, "coordinates": [103, 96]},
        "b": {"index": 1, "coordinates": [100, 140]},
    }

    async def predict(payload: dict[str, Any]) -> dict[str, Any]:
        if payload["case"] == "c":
            raise RuntimeError("model down")
        return answers[payload["case"]]

    report = await run_golden(GoldenManifest.from_mapping(MANIFEST), predict)

    assert report.total == 3
    assert report.answered == 2
    assert report.correct == 1, "0002 is outside its 2px tolerance"
    assert report.errors == 1
    assert report.accuracy == pytest.approx(0.5)
    assert report.coverage == pytest.approx(2 / 3)
    assert report.passed is False

    per_type = report.per_type()
    assert per_type["hopscotch"]["total"] == 3
    assert per_type["hopscotch"]["accuracy"] == pytest.approx(0.5)

    data = report.to_dict()
    assert data["case_errors"] == [{"case": "0003", "error": "RuntimeError: model down"}]
    assert [m["case"] for m in data["misses"]] == ["0002"]


async def test_run_golden_attaches_images_and_accepts_sync_predictor() -> None:
    seen: list[str] = []

    async def loader(key: str) -> bytes:
        seen.append(key)
        return b"PNG-BYTES"

    def predict(payload: dict[str, Any]) -> dict[str, Any]:
        assert payload["image_b64"] == base64.b64encode(b"PNG-BYTES").decode("ascii")
        assert payload["hint"] == "5 targets"
        return {"index": 2, "coordinates": [10, 20]}

    manifest = GoldenManifest.from_mapping(
        {
            "artifact": "hopscotch-v1",
            "version": "v1",
            "cases": [
                {
                    "id": "img",
                    "type": "hopscotch",
                    "image": "golden/v1/img.png",
                    "payload": {"hint": "5 targets"},
                    "expected": {"index": 2, "coordinates": [10, 20]},
                }
            ],
        }
    )

    report = await run_golden(manifest, predict, loader=loader)

    assert seen == ["golden/v1/img.png"]
    assert report.passed is True
    assert report.accuracy == 1.0
    assert report.coverage == 1.0


async def test_run_golden_without_loader_fails_the_case_cleanly() -> None:
    manifest = GoldenManifest.from_mapping(
        {"artifact": "hopscotch-v1", "cases": [{"id": "img", "image": "k.png", "expected": {"index": 1}}]}
    )

    report = await run_golden(manifest, lambda payload: {"index": 1})

    assert report.answered == 0
    assert report.coverage == 0.0
    assert "no loader" in report.results[0].error


async def test_run_golden_gate_is_explicit() -> None:
    cases = [
        {"id": f"{i:02d}", "type": "hopscotch", "payload": {"i": i}, "expected": {"index": 0}} for i in range(10)
    ]

    async def predict(payload: dict[str, Any]) -> dict[str, Any]:
        if payload["i"] == 9:
            raise RuntimeError("abstain")
        return {"index": 1 if payload["i"] == 8 else 0}

    manifest = GoldenManifest.from_mapping({"artifact": "hopscotch-v1", "cases": cases})

    strict = await run_golden(manifest, predict)
    assert strict.accuracy == pytest.approx(8 / 9)
    assert strict.coverage == pytest.approx(0.9)
    assert strict.passed is False, "0.889 accuracy is below the 0.95 gate"

    beta = await run_golden(manifest, predict, min_accuracy=0.85, min_coverage=0.6)
    assert beta.passed is True, "the same run clears a beta gate"


def test_golden_report_of_nothing_is_not_a_pass() -> None:
    report = GoldenReport(artifact="hopscotch-v1", version="v1", total=0, results=[])
    assert report.accuracy == 0.0
    assert report.coverage == 0.0
    assert report.passed is False
    assert report.per_type() == {}


def test_mismatched_fields_shapes() -> None:
    assert mismatched_fields({"index": 2}, {"index": 2}) == ()
    assert mismatched_fields({"index": 2}, {"index": 3}) == ("index",)
    assert mismatched_fields({"index": 2}, {"other": 2}) == ("index",)
    assert mismatched_fields({"index": 2}, None) == ("index",)

    assert mismatched_fields({"coordinates": [10, 10]}, {"coordinates": [16, 10]}) == ()
    assert mismatched_fields({"coordinates": [10, 10]}, {"coordinates": [19, 10]}) == ("coordinates",)
    assert mismatched_fields({"coordinates": [10, 10]}, {"coordinates": [16, 10]}, tolerance_px=2) == ("coordinates",)
    assert mismatched_fields({"bbox": [0, 0, 10, 10]}, {"bbox": [1, 1, 11, 11]}) == ()
    assert mismatched_fields({"bbox": [0, 0, 10, 10]}, {"bbox": [1, 1, 11]}) == ("bbox",)
    assert mismatched_fields({"label": "bus"}, {"label": "BUS"}) == ("label",)

# ---- golden: CLI wiring ----------------------------------------------------


def test_cli_golden_runs_a_predictor_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Any, capsys: Any) -> None:
    import argparse
    import json
    import sys
    import types

    from captcha.services.pipeline import cli

    module = types.ModuleType("fake_golden_predictor")
    module.predict = lambda payload: {"index": 2, "coordinates": [10, 20]}
    monkeypatch.setitem(sys.modules, "fake_golden_predictor", module)

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "artifact": "hopscotch-v1",
                "version": "v1",
                "cases": [
                    {"id": "1", "type": "hopscotch", "expected": {"index": 2, "coordinates": [10, 20]}},
                    {"id": "2", "type": "hopscotch", "expected": {"index": 2, "coordinates": [12, 21]}},
                ],
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        manifest=str(manifest_path),
        predict="fake_golden_predictor:predict",
        loader="",
        min_accuracy=None,
        min_coverage=None,
        concurrency=2,
    )

    import asyncio

    code = asyncio.run(cli._run_golden(args))

    assert code == 0
    # structlog (text or JSON, depending on what configured it) shares stdout
    # with the report, so match on content instead of parsing the whole stream.
    raw = capsys.readouterr().out
    assert '"passed": true' in raw
    assert '"accuracy": 1.0' in raw
    assert '"version": "v1"' in raw


def test_cli_golden_reports_setup_failures(tmp_path: Any) -> None:
    import argparse
    import asyncio

    from captcha.services.pipeline import cli

    base = {"min_accuracy": None, "min_coverage": None, "concurrency": 1, "loader": ""}
    missing_manifest = argparse.Namespace(manifest="", predict="x:y", **base)
    assert asyncio.run(cli._run_golden(missing_manifest)) == 2

    bad_spec = argparse.Namespace(manifest=str(tmp_path / "m.json"), predict="no-colon", **base)
    assert asyncio.run(cli._run_golden(bad_spec)) == 2

    good_spec = argparse.Namespace(manifest=str(tmp_path / "absent.json"), predict="json:dumps", **base)
    assert asyncio.run(cli._run_golden(good_spec)) == 2

def test_cli_check_model_prints_the_bundle_report(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    from types import SimpleNamespace

    from captcha.adapters.weights.artifact import ArtifactCheck
    from captcha.services.pipeline import cli

    check = ArtifactCheck(name="hopscotch-v1", version="v1", ok=True)

    class FakeRegistry:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def check_artifact(self, name: str, version: str = "latest") -> ArtifactCheck:
            assert (name, version) == ("hopscotch-v1", "v1")
            return check

    monkeypatch.setattr("captcha.adapters.weights.Registry", FakeRegistry)
    settings = SimpleNamespace(
        s3_endpoint="http://minio.invalid",
        s3_bucket="models",
        s3_access_key="key",
        s3_secret_key="secret",
        s3_region="us-east-1",
    )

    assert cli._run_check_model(settings, name="hopscotch-v1", version="v1") == 0
    import json

    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True
    assert printed["name"] == "hopscotch-v1"

    assert cli._run_check_model(settings, name="", version="v1") == 2
