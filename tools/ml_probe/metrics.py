"""Offline metrics for visual captcha prototypes.

Reads two JSONL files — ground truth and predictions — joined by `id`, computes
task metrics, latency percentiles and Wilson confidence intervals. Stdlib only:
no torch, no network, runs on any machine and in CI.

Schema and metric definitions: `tools/ml_probe/README.md`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_TOL = 0.02  # fraction of the image diagonal
Z95 = 1.96
TASKS = ("grid", "click", "hopscotch")

Record = dict[str, Any]


def load_jsonl(path: str | Path) -> list[Record]:
    """Read a JSONL file, skipping blank lines, failing loudly on bad rows."""
    records: list[Record] = []
    text = Path(path).read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{lineno}: invalid JSON: {e}") from e
        if not isinstance(rec, dict) or "id" not in rec:
            raise ValueError(f"{path}:{lineno}: expected an object with 'id'")
        records.append(rec)
    return records


def _index(records: list[Record], label: str) -> dict[str, Record]:
    out: dict[str, Record] = {}
    for rec in records:
        rid = str(rec["id"])
        if rid in out:
            raise ValueError(f"{label}: duplicate id {rid!r}")
        out[rid] = rec
    return out


def _points(raw: Any, where: str) -> list[tuple[float, float]]:
    if not isinstance(raw, list):
        raise ValueError(f"{where}: expected a list of [x, y]")
    pts: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"{where}: bad point {item!r}")
        x, y = float(item[0]), float(item[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(f"{where}: non-finite point {item!r}")
        pts.append((x, y))
    return pts


def _indices(raw: Any, where: str) -> set[int]:
    if not isinstance(raw, list):
        raise ValueError(f"{where}: expected a list of tile indices")
    out: set[int] = set()
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{where}: tile index must be int, got {item!r}")
        out.add(item)
    return out


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _wilson(k: int, n: int) -> list[float | None]:
    """95% Wilson interval for a proportion; robust at k=0 and k=n."""
    if n == 0:
        return [None, None]
    p = k / n
    denom = 1 + Z95 * Z95 / n
    centre = (p + Z95 * Z95 / (2 * n)) / denom
    margin = Z95 * math.sqrt(p * (1 - p) / n + Z95 * Z95 / (4 * n * n)) / denom
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile; q in [0, 1]."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _latency(values: list[float]) -> dict[str, float | None]:
    return {
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "mean": sum(values) / len(values) if values else None,
    }


def _match(
    expected: list[tuple[float, float]],
    predicted: list[tuple[float, float]],
    tol: float,
) -> tuple[int, int, int, list[float]]:
    """Greedy nearest matching inside `tol` pixels. Returns tp, fp, fn, distances."""
    used = [False] * len(predicted)
    tp = 0
    distances: list[float] = []
    for point in expected:
        best = -1
        best_dist = math.inf
        for j, candidate in enumerate(predicted):
            if used[j]:
                continue
            dist = math.dist(point, candidate)
            if dist < best_dist:
                best_dist = dist
                best = j
        distances.append(best_dist)
        if best >= 0 and best_dist <= tol:
            used[best] = True
            tp += 1
    return tp, sum(1 for flag in used if not flag), len(expected) - tp, distances


def _grid(expected: Any, predicted: Any, where: str) -> dict[str, Any]:
    gold = _indices(expected, f"{where}.expected")
    got = _indices(predicted, f"{where}.predicted")
    tp = len(gold & got)
    precision, recall, f1 = _prf(tp, len(got - gold), len(gold - got))
    return {
        "exact": gold == got,
        "tp": tp,
        "fp": len(got - gold),
        "fn": len(gold - got),
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _click(expected: Any, predicted: Any, diag: float, tol: float, where: str) -> dict[str, Any]:
    gold = _points(expected, f"{where}.expected")
    got = _points(predicted, f"{where}.predicted")
    tp, fp, fn, distances = _match(gold, got, tol * diag)
    precision, recall, f1 = _prf(tp, fp, fn)
    matched = [d for d in distances if d <= tol * diag]
    return {
        "hit": tp == len(gold) and fp == 0,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mean_error_px": sum(matched) / len(matched) if matched else None,
    }


def _hopscotch(expected: Any, predicted: Any, diag: float, tol: float, where: str) -> dict[str, Any]:
    if not isinstance(expected, dict) or not isinstance(predicted, dict):
        raise ValueError(f"{where}: expected an object with 'index' and 'point'")
    for field in ("index", "point"):
        if field not in expected or field not in predicted:
            raise ValueError(f"{where}: missing {field!r}")
    index_ok = int(expected["index"]) == int(predicted["index"])
    dist = math.dist(
        _points([expected["point"]], f"{where}.expected.point")[0],
        _points([predicted["point"]], f"{where}.predicted.point")[0],
    )
    coord_ok = dist <= tol * diag
    return {
        "index_ok": index_ok,
        "coord_ok": coord_ok,
        "joint": index_ok and coord_ok,
        "error_px": dist,
    }


def _diag(rec: Record, other: Record, where: str) -> float:
    size = rec.get("image_size") or other.get("image_size")
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        raise ValueError(f"{where}: 'image_size': [width, height] is required")
    width, height = float(size[0]), float(size[1])
    if not (width > 0 and height > 0):
        raise ValueError(f"{where}: bad image_size {size!r}")
    return math.hypot(width, height)


def evaluate(golden: list[Record], predictions: list[Record], tol: float = DEFAULT_TOL) -> dict[str, Any]:
    """Join golden and predictions by id and aggregate per-task metrics."""
    gold = _index(golden, "golden")
    pred = _index(predictions, "predictions")
    missing = sorted(set(gold) - set(pred))
    extra = sorted(set(pred) - set(gold))
    if missing or extra:
        raise ValueError(f"id mismatch: missing={missing[:5]} extra={extra[:5]}")

    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    latency: list[float] = []
    latency_by_task: dict[str, list[float]] = defaultdict(list)
    abstained = 0

    for rid, g in gold.items():
        p = pred[rid]
        task = g.get("task") or p.get("task")
        if task not in TASKS:
            raise ValueError(f"{rid}: unknown task {task!r}")
        if "expected" not in g:
            raise ValueError(f"{rid}: golden row has no 'expected'")
        where = f"{rid} ({task})"
        lat = p.get("latency_ms")
        if lat is not None and (isinstance(lat, bool) or not isinstance(lat, (int, float)) or lat < 0):
            raise ValueError(f"{where}: bad latency_ms {lat!r}")
        is_abstain = bool(p.get("abstain", False))
        if is_abstain:
            abstained += 1
            continue
        if "predicted" not in p:
            raise ValueError(f"{where}: prediction has neither 'predicted' nor abstain")
        if "image_size" not in g and "image_size" not in p and task != "grid":
            raise ValueError(f"{where}: 'image_size' is required for {task}")
        if task == "grid":
            result = _grid(g["expected"], p["predicted"], where)
        elif task == "click":
            result = _click(g["expected"], p["predicted"], _diag(g, p, where), tol, where)
        else:
            result = _hopscotch(g["expected"], p["predicted"], _diag(g, p, where), tol, where)
        rows[task].append(result)
        if lat is not None:
            latency.append(float(lat))
            latency_by_task[task].append(float(lat))

    tasks: dict[str, Any] = {}
    for task, items in sorted(rows.items()):
        tasks[task] = _aggregate(task, items, latency_by_task[task])
    tasks["_all"] = {
        "records": len(gold),
        "answered": sum(len(items) for items in rows.values()),
        "abstained": abstained,
        "coverage": (len(gold) - abstained) / len(gold) if gold else 0.0,
        "latency_ms": _latency(latency),
    }
    return {"config": {"tol": tol}, "tasks": tasks}


def _aggregate(task: str, items: list[dict[str, Any]], latency: list[float]) -> dict[str, Any]:
    n = len(items)
    out: dict[str, Any] = {"n": n, "latency_ms": _latency(latency)}
    if n == 0:
        return out
    if task == "grid":
        exact = sum(1 for r in items if r["exact"])
        tp = sum(r["tp"] for r in items)
        fp = sum(r["fp"] for r in items)
        fn = sum(r["fn"] for r in items)
        precision, recall, f1 = _prf(tp, fp, fn)
        out.update(
            {
                "exact_match": exact / n,
                "exact_match_ci": _wilson(exact, n),
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "mean_f1": sum(r["f1"] for r in items) / n,
            }
        )
    elif task == "click":
        hits = sum(1 for r in items if r["hit"])
        tp = sum(r["tp"] for r in items)
        fp = sum(r["fp"] for r in items)
        fn = sum(r["fn"] for r in items)
        precision, recall, f1 = _prf(tp, fp, fn)
        errors = [r["mean_error_px"] for r in items if r["mean_error_px"] is not None]
        out.update(
            {
                "hit_rate": hits / n,
                "hit_rate_ci": _wilson(hits, n),
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "mean_error_px": sum(errors) / len(errors) if errors else None,
            }
        )
    else:
        index_ok = sum(1 for r in items if r["index_ok"])
        coord_ok = sum(1 for r in items if r["coord_ok"])
        joint = sum(1 for r in items if r["joint"])
        out.update(
            {
                "index_accuracy": index_ok / n,
                "coord_accuracy": coord_ok / n,
                "joint_rate": joint / n,
                "joint_rate_ci": _wilson(joint, n),
                "mean_error_px": sum(r["error_px"] for r in items) / n,
            }
        )
    return out


def _format(result: dict[str, Any]) -> str:
    lines: list[str] = []
    all_rows = result["tasks"]["_all"]
    lines.append(
        f"records={all_rows['records']} answered={all_rows['answered']} "
        f"abstained={all_rows['abstained']} coverage={all_rows['coverage']:.3f}"
    )
    for task, row in result["tasks"].items():
        if task == "_all":
            continue
        parts = [f"{task}: n={row['n']}"]
        for key in (
            "exact_match",
            "hit_rate",
            "index_accuracy",
            "coord_accuracy",
            "joint_rate",
            "precision",
            "recall",
            "f1",
        ):
            if key in row:
                parts.append(f"{key}={row[key]:.3f}")
        if row.get("mean_error_px") is not None:
            parts.append(f"mean_err={row['mean_error_px']:.1f}px")
        lat = row["latency_ms"]
        if lat["p95"] is not None:
            parts.append(f"p50={lat['p50']:.1f}ms p95={lat['p95']:.1f}ms")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def _failures(result: dict[str, Any], specs: list[str]) -> list[str]:
    failures: list[str] = []
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"--fail-under expects task.metric=value, got {spec!r}")
        key, raw = spec.split("=", 1)
        task, _, metric = key.partition(".")
        if not task or not metric:
            raise ValueError(f"--fail-under expects task.metric=value, got {spec!r}")
        row = result["tasks"].get(task)
        if row is None or metric not in row or row[metric] is None:
            raise ValueError(f"--fail-under: no metric {key!r}")
        if row[metric] < float(raw):
            failures.append(f"{key}={row[metric]:.3f} < {float(raw):.3f}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline metrics for captcha visual prototypes.")
    parser.add_argument("golden", help="JSONL with ground truth")
    parser.add_argument("predictions", help="JSONL with predictions")
    parser.add_argument(
        "--tol",
        type=float,
        default=DEFAULT_TOL,
        help=f"point tolerance as a fraction of the image diagonal (default {DEFAULT_TOL})",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="print the full result as JSON")
    parser.add_argument(
        "--fail-under",
        action="append",
        default=[],
        metavar="TASK.METRIC=VALUE",
        help="exit 1 if a metric is below VALUE; repeatable",
    )
    args = parser.parse_args(argv)

    result = evaluate(load_jsonl(args.golden), load_jsonl(args.predictions), tol=args.tol)
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_format(result))
    failures = _failures(result, args.fail_under)
    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
