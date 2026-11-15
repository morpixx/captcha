"""Training kit, step 4: GoldVal30 + client validation evaluation.

The metric computation stays in hopscotch_v2 (same code that produced 70.5%);
this module only drives the existing evaluate command per role, collects the
report metrics into one file the export step can embed, and offers a pure
scorer for saved predictions so the numbers can be re-checked offline.

Targets from the production contract: 95% accuracy at 80% coverage on the
frozen development validation. Nothing here lowers them.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TARGET_ACCURACY = 0.95
TARGET_COVERAGE = 0.80

#: Roles the kit evaluates. client_val is 400 waves of development validation,
#: gold_val is the 30 hand-labelled waves; both are frozen and never trained on.
DEFAULT_ROLES: tuple[str, ...] = ("gold_val", "client_val")

METRIC_KEYS = (
    "total",
    "correct",
    "answer_accuracy",
    "accepted_total",
    "selective_accuracy",
    "coverage",
    "nll",
    "label_evidence",
)


@dataclass(frozen=True)
class RoleResult:
    role: str
    output: Path
    command: list[str]
    metrics: dict[str, Any]


def evaluate_command(
    root: str | Path,
    *,
    checkpoint: str | Path,
    role: str,
    output: str | Path,
    config: str = "configs/v1-spatial.yaml",
    release: str | Path | None = None,
    python: str | None = None,
) -> list[str]:
    base = Path(root)
    executable = python or sys.executable
    command = [executable, str(base / "scripts" / "v1.py"), "--config", config, "evaluate"]
    command += ["--checkpoint", str(Path(checkpoint).resolve()), "--role", role]
    command += ["--output", str(Path(output).resolve())]
    if release is not None:
        command += ["--release", str(Path(release).resolve())]
    return command


def summarize_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the comparable numbers, drop per-wave records."""
    metrics = report.get("metrics")
    if not isinstance(metrics, Mapping):
        raise ValueError("evaluation report has no metrics block")
    summary = {key: metrics.get(key) for key in METRIC_KEYS if key in metrics}
    summary["role"] = report.get("role")
    summary["model_id"] = report.get("model_id")
    summary["manifest_hash"] = report.get("manifest_hash")
    return summary


def score_predictions(
    predicted: Mapping[str, int],
    truth: Mapping[str, int],
    *,
    index_base: int = 0,
) -> dict[str, Any]:
    """Offline agreement score. Both sides are card indices in the given base."""
    if index_base not in (0, 1):
        raise ValueError("index_base must be 0 or 1")
    common = sorted(set(predicted) & set(truth))
    correct = sum(1 for wave in common if int(predicted[wave]) == int(truth[wave]))
    total = len(common)
    return {
        "total": total,
        "correct": correct,
        "answer_accuracy": (correct / total) if total else None,
        "index_base": index_base,
        "target_accuracy": TARGET_ACCURACY,
        "target_met": bool(total) and correct / total >= TARGET_ACCURACY,
    }


def run_role(
    root: str | Path,
    *,
    checkpoint: str | Path,
    role: str,
    output: str | Path,
    config: str = "configs/v1-spatial.yaml",
    release: str | Path | None = None,
    python: str | None = None,
    runner: Any = None,
) -> RoleResult:
    """Execute one role evaluation and parse its report."""
    command = evaluate_command(
        root,
        checkpoint=checkpoint,
        role=role,
        output=output,
        config=config,
        release=release,
        python=python,
    )
    run = runner or _run
    code = run(command, Path(root))
    if code not in (0, None):
        raise RuntimeError(f"evaluate {role} exited with {code}")
    report = json.loads(Path(output).read_text(encoding="utf-8"))
    return RoleResult(role=role, output=Path(output), command=command, metrics=summarize_report(report))


def run_matrix(
    root: str | Path,
    *,
    checkpoint: str | Path,
    out_dir: str | Path,
    config: str = "configs/v1-spatial.yaml",
    release: str | Path | None = None,
    roles: Sequence[str] = DEFAULT_ROLES,
    python: str | None = None,
    runner: Any = None,
) -> dict[str, Any]:
    """Evaluate every requested role and write one metrics.json for export."""
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    for role in roles:
        output = target / f"{role}.json"
        if output.exists():
            output.unlink()  # upstream refuses to overwrite an immutable report
        result = run_role(
            root,
            checkpoint=checkpoint,
            role=role,
            output=output,
            config=config,
            release=release,
            python=python,
            runner=runner,
        )
        results[role] = result.metrics
    summary = {
        "targets": {"accuracy": TARGET_ACCURACY, "coverage": TARGET_COVERAGE},
        "roles": results,
    }
    (target / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _run(command: Iterable[str], cwd: Path) -> int:
    import subprocess

    completed = subprocess.run(list(command), cwd=str(cwd), check=False)
    return completed.returncode


__all__ = [
    "DEFAULT_ROLES",
    "METRIC_KEYS",
    "TARGET_ACCURACY",
    "TARGET_COVERAGE",
    "RoleResult",
    "evaluate_command",
    "run_matrix",
    "run_role",
    "score_predictions",
    "summarize_report",
]
