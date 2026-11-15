"""Golden-set evaluation for visual solvers: accuracy and coverage over a manifest.

A golden manifest is JSON the training side writes next to the artifact:

    {"artifact": "hopscotch-v1", "version": "v1",
     "tolerance_px": 8, "min_accuracy": 0.95, "min_coverage": 0.80,
     "cases": [{"id": "0001", "type": "hopscotch",
                "image": "golden/hopscotch/v1/0001.png",
                "payload": {"hint": "5 targets"},
                "expected": {"index": 3, "coordinates": [412, 288]},
                "tolerance_px": 6, "weight": 1.0, "tags": ["goldval"]}]}

run_golden() pulls image bytes through an injected loader (the registry in prod,
a fake in tests), attaches them to the payload as base64, runs the predictor per
case, and reports:

* accuracy - correct / answered. A case whose predictor raised is not counted as
  wrong, it is not counted at all; that is what coverage is for.
* coverage - answered / total. A manifest the model cannot even attempt is a
  coverage failure, not an accuracy failure.

Thresholds are explicit, so a beta gate (0.90/0.60) is one argument away from the
production gate (0.95/0.80 in the production contract). Matching: numeric list
fields match per axis within tolerance_px; everything else matches exactly.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from captcha.domain.errors import ConfigError
from captcha.services.pipeline.golden_match import DEFAULT_TOLERANCE_PX, mismatched_fields

logger = structlog.get_logger(__name__)

#: production contract final gate for hopscotch: 95% correct on 80% attempted.
DEFAULT_MIN_ACCURACY = 0.95
DEFAULT_MIN_COVERAGE = 0.80
#: payload key a solver reads its image from (see docs/protocol.md, hopscotch)
DEFAULT_IMAGE_FIELD = "image_b64"

Predictor = Callable[[dict[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
Loader = Callable[[str], bytes | Awaitable[bytes]]


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    type_: str
    payload: dict[str, Any]
    expected: dict[str, Any]
    image: str | None = None
    tolerance_px: float = DEFAULT_TOLERANCE_PX
    weight: float = 1.0
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.case_id, "type": self.type_, "expected": dict(self.expected)}
        if self.image:
            out["image"] = self.image
        if self.payload:
            out["payload"] = dict(self.payload)
        return out


@dataclass(slots=True)
class GoldenManifest:
    artifact: str
    version: str
    cases: tuple[GoldenCase, ...]
    tolerance_px: float = DEFAULT_TOLERANCE_PX
    min_accuracy: float = DEFAULT_MIN_ACCURACY
    min_coverage: float = DEFAULT_MIN_COVERAGE
    source: str = ""

    @classmethod
    def from_mapping(cls, doc: Mapping[str, Any], *, source: str = "") -> GoldenManifest:
        if not isinstance(doc, Mapping):
            raise ConfigError("golden manifest must be a JSON object", context={"source": source})
        raw_cases = doc.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ConfigError("golden manifest needs a non-empty 'cases' list", context={"source": source})
        tolerance = _tolerance(doc.get("tolerance_px", DEFAULT_TOLERANCE_PX))
        cases = tuple(_parse_case(raw, index, tolerance) for index, raw in enumerate(raw_cases))
        seen: dict[str, int] = {}
        for case in cases:
            seen[case.case_id] = seen.get(case.case_id, 0) + 1
        duplicates = sorted(case_id for case_id, count in seen.items() if count > 1)
        if duplicates:
            raise ConfigError("golden manifest has duplicate case ids", context={"ids": duplicates})
        return cls(
            artifact=str(doc.get("artifact") or ""),
            version=str(doc.get("version") or ""),
            cases=cases,
            tolerance_px=tolerance,
            min_accuracy=_ratio(doc.get("min_accuracy", DEFAULT_MIN_ACCURACY)),
            min_coverage=_ratio(doc.get("min_coverage", DEFAULT_MIN_COVERAGE)),
            source=source,
        )

    @classmethod
    def from_bytes(cls, blob: bytes, *, source: str = "") -> GoldenManifest:
        try:
            doc = json.loads(blob)
        except ValueError as exc:
            raise ConfigError(f"golden manifest is not valid JSON: {exc}", context={"source": source}) from exc
        return cls.from_mapping(doc, source=source)

    @classmethod
    def from_file(cls, path: str | Path) -> GoldenManifest:
        target = Path(path)
        try:
            blob = target.read_bytes()
        except OSError as exc:
            raise ConfigError(f"cannot read golden manifest: {exc}", context={"path": str(target)}) from exc
        return cls.from_bytes(blob, source=str(target))

    @property
    def total(self) -> int:
        return len(self.cases)

@dataclass(slots=True)
class CaseResult:
    case_id: str
    type_: str
    answered: bool
    ok: bool
    expected: dict[str, Any] = field(default_factory=dict)
    predicted: Any = None
    mismatched: tuple[str, ...] = ()
    error: str = ""
    latency_ms: float = 0.0
    weight: float = 1.0
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "case": self.case_id,
            "type": self.type_,
            "ok": self.ok,
            "answered": self.answered,
            "latency_ms": round(self.latency_ms, 2),
        }
        if self.tags:
            out["tags"] = list(self.tags)
        if self.weight != 1.0:
            out["weight"] = self.weight
        if self.error:
            out["error"] = self.error
        if self.mismatched:
            out.update(mismatched=list(self.mismatched), expected=dict(self.expected), predicted=self.predicted)
        return out


@dataclass(slots=True)
class GoldenReport:
    artifact: str
    version: str
    total: int
    results: list[CaseResult]
    min_accuracy: float = DEFAULT_MIN_ACCURACY
    min_coverage: float = DEFAULT_MIN_COVERAGE
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def answered(self) -> int:
        return sum(1 for r in self.results if r.answered)

    @property
    def correct(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.error)

    @property
    def accuracy(self) -> float:
        answered = self.answered
        return self.correct / answered if answered else 0.0

    @property
    def coverage(self) -> float:
        return self.answered / self.total if self.total else 0.0

    @property
    def passed(self) -> bool:
        return self.total > 0 and self.accuracy >= self.min_accuracy and self.coverage >= self.min_coverage

    @property
    def duration_sec(self) -> float:
        return round(self.finished_at - self.started_at, 3)

    def per_type(self) -> dict[str, dict[str, Any]]:
        buckets: dict[str, list[CaseResult]] = {}
        for result in self.results:
            buckets.setdefault(result.type_ or "unknown", []).append(result)
        out: dict[str, dict[str, Any]] = {}
        for type_, results in sorted(buckets.items()):
            answered = sum(1 for r in results if r.answered)
            correct = sum(1 for r in results if r.ok)
            out[type_] = {
                "total": len(results),
                "answered": answered,
                "correct": correct,
                "accuracy": round(correct / answered, 4) if answered else 0.0,
                "coverage": round(answered / len(results), 4),
            }
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "version": self.version,
            "total": self.total,
            "answered": self.answered,
            "correct": self.correct,
            "errors": self.errors,
            "accuracy": round(self.accuracy, 4),
            "coverage": round(self.coverage, 4),
            "min_accuracy": self.min_accuracy,
            "min_coverage": self.min_coverage,
            "passed": self.passed,
            "duration_sec": self.duration_sec,
            "per_type": self.per_type(),
            "case_errors": [{"case": r.case_id, "error": r.error} for r in self.results if r.error],
            "misses": [r.to_dict() for r in self.results if r.answered and not r.ok],
        }


async def run_golden(
    manifest: GoldenManifest,
    predict: Predictor,
    *,
    loader: Loader | None = None,
    image_field: str = DEFAULT_IMAGE_FIELD,
    concurrency: int = 4,
    min_accuracy: float | None = None,
    min_coverage: float | None = None,
) -> GoldenReport:
    """Run predict over every case; a single broken case never aborts the run."""
    started = time.time()
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def one(case: GoldenCase) -> CaseResult:
        async with semaphore:
            return await _run_case(case, predict, loader, image_field)

    results = await asyncio.gather(*(one(case) for case in manifest.cases))
    report = GoldenReport(
        artifact=manifest.artifact,
        version=manifest.version,
        total=len(manifest.cases),
        results=list(results),
        min_accuracy=manifest.min_accuracy if min_accuracy is None else _ratio(min_accuracy),
        min_coverage=manifest.min_coverage if min_coverage is None else _ratio(min_coverage),
        started_at=started,
        finished_at=time.time(),
    )
    logger.info(
        "pipeline.golden.done",
        artifact=report.artifact,
        version=report.version,
        accuracy=round(report.accuracy, 4),
        coverage=round(report.coverage, 4),
        passed=report.passed,
    )
    return report


# ---- internals ---------------------------------------------------------


async def _run_case(case: GoldenCase, predict: Predictor, loader: Loader | None, image_field: str) -> CaseResult:
    payload = dict(case.payload)
    started = time.perf_counter()
    try:
        if case.image:
            if loader is None:
                raise ConfigError(f"case {case.case_id} references an image but no loader was given")
            blob = await _maybe_await(loader(case.image))
            payload[image_field] = base64.b64encode(bytes(blob)).decode("ascii")
        prediction = await _maybe_await(predict(payload))
    except Exception as exc:  # one broken case must not abort a 400-case run
        return _failed(case, f"{type(exc).__name__}: {exc}", started)

    latency_ms = (time.perf_counter() - started) * 1000.0
    if prediction is None:
        return _failed(case, "predictor returned nothing (abstained)", started)
    if not isinstance(prediction, Mapping):
        return _failed(case, f"predictor returned {type(prediction).__name__}, expected a mapping", started)

    mismatched = mismatched_fields(case.expected, prediction, tolerance_px=case.tolerance_px)
    return CaseResult(
        case_id=case.case_id,
        type_=case.type_,
        answered=True,
        ok=not mismatched,
        expected=dict(case.expected),
        predicted=dict(prediction),
        mismatched=mismatched,
        latency_ms=latency_ms,
        weight=case.weight,
        tags=case.tags,
    )


def _failed(case: GoldenCase, error: str, started: float) -> CaseResult:
    return CaseResult(
        case_id=case.case_id,
        type_=case.type_,
        answered=False,
        ok=False,
        expected=dict(case.expected),
        error=error,
        latency_ms=(time.perf_counter() - started) * 1000.0,
        weight=case.weight,
        tags=case.tags,
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _parse_case(raw: Any, index: int, default_tolerance: float) -> GoldenCase:
    if not isinstance(raw, Mapping):
        raise ConfigError(f"golden case #{index} must be an object", context={"case": index})
    expected = raw.get("expected")
    if not isinstance(expected, Mapping) or not expected:
        raise ConfigError(f"golden case #{index} needs a non-empty 'expected' object", context={"case": index})
    case_id = str(raw.get("id") or raw.get("case_id") or f"case-{index:04d}")
    payload = raw.get("payload") or {}
    if not isinstance(payload, Mapping):
        raise ConfigError(f"golden case {case_id}: 'payload' must be an object", context={"case": case_id})
    image = raw.get("image")
    if image is not None and not isinstance(image, str):
        raise ConfigError(f"golden case {case_id}: 'image' must be a string key", context={"case": case_id})
    tags = raw.get("tags") or ()
    if not isinstance(tags, (list, tuple)):
        raise ConfigError(f"golden case {case_id}: 'tags' must be a list", context={"case": case_id})
    return GoldenCase(
        case_id=case_id,
        type_=str(raw.get("type") or ""),
        payload=dict(payload),
        expected=dict(expected),
        image=image,
        tolerance_px=_tolerance(raw.get("tolerance_px", default_tolerance)),
        weight=float(raw.get("weight", 1.0)),
        tags=tuple(str(tag) for tag in tags),
    )


def _tolerance(value: Any) -> float:
    try:
        tol = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"tolerance_px must be a number, got {value!r}") from exc
    if not math.isfinite(tol):
        raise ConfigError(f"tolerance_px must be finite, got {value!r}")
    return max(0.0, tol)


def _ratio(value: Any) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"threshold must be a number, got {value!r}") from exc
    if not math.isfinite(ratio):
        raise ConfigError(f"threshold must be finite, got {value!r}")
    return min(1.0, max(0.0, ratio))
